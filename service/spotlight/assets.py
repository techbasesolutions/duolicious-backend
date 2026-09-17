"""Content-hashed immutable Spotlight image keys and their compare-and-set
attach (Wave 2 F06 part 2).

The key embeds the revision it was rendered for and the sha256 of the
bytes it names, so two uploads for the same request/platform/revision that
happen to carry different bytes never collide on one key, and a re-upload
of the exact same bytes is idempotent (same key, same object). Every
function here runs inside the caller's api_tx; none opens one.
"""
from __future__ import annotations

from typing import Optional

from service.spotlight.revisions import attach_render
from service.spotlight.storage import card_image_extension


def asset_key(request_key: str, revision_id: int, sha256: str, platform: str,
              content_type: str = 'image/png') -> str:
    """Wave 3d Task 6: the extension follows the stored content type, `.jpg`
    for the JPEG cards the admin renders now and `.png` for an upload through
    the old `png_base64` field. Cleanup and retention match keys exactly, as
    stored on the rows, so either extension is found and deleted."""
    ext = card_image_extension(content_type)
    return f"spotlight/{request_key}/{revision_id}-{sha256[:16]}-{platform}.{ext}"


def attach_platform_image(tx, request_key: str, platform: str, revision_id: int,
                           key: str, url: str, sha256: str) -> tuple[str, Optional[str]]:
    """Compare-and-set: stamps this row's own image columns only when it is
    still pinned to the exact revision the upload was rendered against, and
    still in a status that may have its artwork replaced. The route reads
    `revision_id` and `status` before the (network) upload, so a caption
    edit, a re-render, an approve or a cancel landing while the bytes were
    in flight moves the row (or its current_revision_id) out from under
    this WHERE, and zero rows are updated.

    Returns `(outcome, previous_key)`: 'attached' or 'superseded' (0 rows),
    and the image_key this UPDATE displaced (None when the row carried none,
    and always None on 'superseded', which changed nothing). The route's own
    row reflects reality either way, and the caller (not this function)
    decides what happens to whichever object is now orphaned.

    `previous_key` exists because keys are content-hashed (fix wave item 6):
    re-rendering the SAME revision with different bytes produces a different
    key, so a successful attach silently stops naming the object it replaced.
    The superseded path only ever covered the upload that LOST; the one that
    won leaked an object every time, which is the ordinary case while an
    operator iterates on artwork. It is read in the UPDATE's own statement,
    through a `FOR UPDATE` CTE, so no concurrent writer can slip a different
    key in between reading the old value and stamping the new one.

    Fix round 1 (ruling 4): also refuses once the pinned revision already
    has an attached render (its `asset_hash` is set), even though the
    revision/status check alone would still pass. Without this, two
    concurrent uploads for the SAME platform and revision could both clear
    the route's up-front `already_rendered` check before either finished
    uploading (a real network round trip in between); the second to reach
    this UPDATE would otherwise silently overwrite the row behind an
    already-rendered revision with different bytes than the ones the
    revision's own image columns point at.

    Task 5 residual (re-review): also refuses a row parked with its
    delivery unresolved (`delivery_state` `attempting` or
    `delivery_unknown`) -- the same rows `create_revision`'s un-render step
    leaves untouched because a live post may be behind them. Attaching a
    fresh upload there would overwrite the artwork that post shows.

    Wave 3d Task 4: a successful attach also clears this row's render
    backoff (`render_attempts`, `render_next_attempt_at`, `render_error`) in
    the same UPDATE. A render reported failed afterwards starts again from
    15 minutes. A sibling platform row keeps its own state until its own
    upload attaches."""
    row = tx.execute(
        """WITH prev AS (
               SELECT id, image_key FROM publishing_queue
                WHERE request_key = %(rk)s AND platform = %(pl)s
                  FOR UPDATE
           )
           UPDATE publishing_queue
               SET image_key = %(k)s, image_url = %(u)s, image_sha256 = %(h)s, updated_at = NOW(),
                   render_attempts = 0, render_next_attempt_at = NULL, render_error = NULL
              FROM prev
             WHERE publishing_queue.id = prev.id
               AND current_revision_id = %(rev)s
               AND status IN ('awaiting_member', 'awaiting_render', 'review')
               AND (delivery_state IS NULL OR delivery_state NOT IN ('attempting', 'delivery_unknown'))
               AND NOT EXISTS (
                     SELECT 1 FROM spotlight_revision r
                      WHERE r.id = publishing_queue.current_revision_id AND r.asset_hash IS NOT NULL)
         RETURNING prev.image_key AS previous_key""",
        dict(k=key, u=url, h=sha256, rk=request_key, pl=platform, rev=revision_id)).fetchone()
    if not row:
        return 'superseded', None
    return 'attached', row['previous_key']


def complete_render_if_ready(tx, request_key: str, revision_id: int) -> bool:
    """One render per revision (carried over from Task 2): the platform
    upload that completes the set stamps the revision's asset_hash/image
    columns, once. Pinned to the facebook row's own hash/key/url when one
    exists, else the first row's, so the member's preview never depends on
    which platform happened to finish uploading last.

    Fix round 1 (ruling 3): only rows still in an uploadable status
    (`awaiting_member`, `awaiting_render`, `review`) are considered at all --
    a terminal (`published`, `cancelled`) or in-flight (`scheduled`,
    `processing`) sibling is ignored outright, not required to match. A
    roundup's two platform rows can now complete independently (Task 5's
    per-platform `record_receipt`, and withdrawal's re-issue step), so a
    sibling that already published on an OLDER revision must never block --
    or be pinned into -- the still-pending row's render on a NEW one.

    Every CONSIDERED row must still be sitting on THIS revision (not a newer
    one created while an upload was in flight) and already carry both its
    own image_key and image_sha256 (fix round 1, ruling 2: a stale
    image_sha256 surviving a reset that cleared image_key would otherwise
    let one platform's old hash silently complete a set the other platform
    was never re-rendered for) -- both stamped together by
    `attach_platform_image` above, one call per platform row. Returns True
    only when this call is the one that attaches the render; a set that is
    not yet complete, or has no considered rows at all, answers False.

    Task 5 residual (re-review): a row parked with its delivery unresolved
    (`delivery_state` `attempting` or `delivery_unknown`) keeps its old
    artwork on purpose and cannot be re-rendered until an operator resolves
    it (see `create_revision`'s un-render scope), yet its `current_revision_id`
    still moves with every re-point -- so it can carry a STALE image_key/sha256
    that happens to match a brand-new revision id. Such a row is excluded from
    the considered set for the per-row match below AND from the facebook pin,
    and its mere presence refuses the whole completion outright: a roundup's
    combined preview must never go out consistent-looking while one of its
    platforms is in a state nobody can yet explain."""
    rows = tx.execute(
        """SELECT platform, image_key, image_url, image_sha256, current_revision_id, delivery_state
             FROM publishing_queue
            WHERE request_key = %(rk)s
              AND status IN ('awaiting_member', 'awaiting_render', 'review')""",
        dict(rk=request_key)).fetchall()
    if not rows or any(r['delivery_state'] in ('attempting', 'delivery_unknown') for r in rows):
        return False
    if any(r['current_revision_id'] != revision_id or not r['image_key'] or not r['image_sha256']
           for r in rows):
        return False
    pinned = next((r for r in rows if r['platform'] == 'facebook'), rows[0])
    try:
        attach_render(tx, revision_id, pinned['image_sha256'], pinned['image_key'], pinned['image_url'])
    except ValueError as e:
        if str(e) != 'already_rendered':
            raise
        # already_rendered: this revision's render was already attached
        # (a duplicate completion path, or a prior call for this exact set).
        # The set is still ready -- just not newly so from this call.
        return False
    return True
