"""Content-hashed immutable Spotlight image keys and their compare-and-set
attach (Wave 2 F06 part 2).

The key embeds the revision it was rendered for and the sha256 of the
bytes it names, so two uploads for the same request/platform/revision that
happen to carry different bytes never collide on one key, and a re-upload
of the exact same bytes is idempotent (same key, same object). Every
function here runs inside the caller's api_tx; none opens one.
"""
from __future__ import annotations

from service.spotlight.revisions import attach_render


def asset_key(request_key: str, revision_id: int, sha256: str, platform: str) -> str:
    return f"spotlight/{request_key}/{revision_id}-{sha256[:16]}-{platform}.png"


def attach_platform_image(tx, request_key: str, platform: str, revision_id: int,
                           key: str, url: str, sha256: str) -> str:
    """Compare-and-set: stamps this row's own image columns only when it is
    still pinned to the exact revision the upload was rendered against, and
    still in a status that may have its artwork replaced. The route reads
    `revision_id` and `status` before the (network) upload, so a caption
    edit, a re-render, an approve or a cancel landing while the bytes were
    in flight moves the row (or its current_revision_id) out from under
    this WHERE, and zero rows are updated.

    Returns 'attached' or 'superseded' (0 rows) -- the route's own row
    reflects reality either way, and the caller (not this function) decides
    what happens to the now-orphaned upload.

    Fix round 1 (ruling 4): also refuses once the pinned revision already
    has an attached render (its `asset_hash` is set), even though the
    revision/status check alone would still pass. Without this, two
    concurrent uploads for the SAME platform and revision could both clear
    the route's up-front `already_rendered` check before either finished
    uploading (a real network round trip in between); the second to reach
    this UPDATE would otherwise silently overwrite the row behind an
    already-rendered revision with different bytes than the ones the
    revision's own image columns point at."""
    cur = tx.execute(
        """UPDATE publishing_queue
               SET image_key = %(k)s, image_url = %(u)s, image_sha256 = %(h)s, updated_at = NOW()
             WHERE request_key = %(rk)s AND platform = %(pl)s
               AND current_revision_id = %(rev)s
               AND status IN ('awaiting_member', 'awaiting_render', 'review')
               AND NOT EXISTS (
                     SELECT 1 FROM spotlight_revision r
                      WHERE r.id = publishing_queue.current_revision_id AND r.asset_hash IS NOT NULL)""",
        dict(k=key, u=url, h=sha256, rk=request_key, pl=platform, rev=revision_id))
    return 'attached' if cur.rowcount else 'superseded'


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
    not yet complete, or has no considered rows at all, answers False."""
    rows = tx.execute(
        """SELECT platform, image_key, image_url, image_sha256, current_revision_id
             FROM publishing_queue
            WHERE request_key = %(rk)s
              AND status IN ('awaiting_member', 'awaiting_render', 'review')""",
        dict(rk=request_key)).fetchall()
    if not rows or any(r['current_revision_id'] != revision_id or not r['image_key'] or not r['image_sha256']
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
