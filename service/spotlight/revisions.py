"""Immutable Spotlight content revisions and revision-bound approval
(Wave 1 remediation, F01/part of F11).

A revision is a frozen snapshot of one request's rendered card: its
caption, chosen photo, participants (roundup tiles), channels and layout.
Any change (a new photo, a new caption) creates a NEW revision rather than
editing the current one, so a member's consent -- recorded against the
specific revision id they saw -- can never silently carry over to content
they never approved. `publishing_queue.current_revision_id` is the one
pointer every row of a request_key shares; `spotlight_revision_consent`'s
primary key is per revision, so an older revision's consent rows are never
touched, and never satisfy a newer one.

Every function here runs inside the caller's api_tx; none opens one.
`settings` is imported lazily from `service.spotlight.queue` inside
`approve_card`, since `queue.py` imports `create_revision` from this
module at load time -- a top-level import back the other way would be a
cycle. `service.spotlight.cleanup` is safe to import at the top: it reaches
`queue` only through its own lazy import, so nothing closes the loop.
"""
from __future__ import annotations

import json
from typing import Optional

from service.config import WEB_BASE_URL
from service.spotlight.cleanup import enqueue_asset_delete

IN_FLIGHT = ('scheduled', 'processing')
# A published or cancelled request is done: its content is either already
# out in the world or dead, and neither state may be quietly rewritten.
TERMINAL = ('published', 'cancelled')


# Rows a `create_revision` re-points, and therefore un-renders: everything of
# the request_key that is not already published or cancelled. Wave 2 Task 5.
_REPOINT_SCOPE = "status NOT IN ('published', 'cancelled')"

# The old artwork of the re-pointed rows, minus anything a published sibling
# still points at (a roundup's two platform rows can share nothing but they
# can share a key after a re-render, and a live post's object must survive).
_Q_OLD_KEYS = f"""
    SELECT DISTINCT q.image_key FROM publishing_queue q
     WHERE q.request_key = %(rk)s AND q.image_key IS NOT NULL
       AND q.{_REPOINT_SCOPE}
       AND NOT EXISTS (SELECT 1 FROM publishing_queue s
                        WHERE s.request_key = %(rk)s AND s.status = 'published'
                          AND s.image_key = q.image_key)
"""

_Q_UNRENDER = f"""
    UPDATE publishing_queue SET image_key = NULL, image_url = NULL, image_sha256 = NULL
     WHERE request_key = %(rk)s AND {_REPOINT_SCOPE}
"""


def _statuses(tx, request_key: str) -> set:
    return {r['status'] for r in tx.execute(
        "SELECT status FROM publishing_queue WHERE request_key = %(rk)s",
        dict(rk=request_key)).fetchall()}


def create_revision(tx, request_key: str, *, caption: str, photo_uuid: Optional[str],
                     participants: list, channels: list, layout_version: str = 'v1',
                     created_by: str, ignore_terminal_siblings: bool = False) -> int:
    """`ignore_terminal_siblings` (Wave 1 F03 fix round 1): a roundup's two
    platform rows can now complete independently (Task 5's `record_receipt`
    lets one platform reach `published` while the other is still `review`
    or `failed`), so the terminal guard below -- written when every row of
    a request_key was assumed to move in lockstep -- would otherwise block
    Spotlight withdrawal from ever re-issuing the still-pending row just
    because its sibling already published. Set only by
    `service.spotlight.withdrawal.withdraw_member`'s re-issue step, which
    has already confirmed (via its own status-scoped query) that the row it
    is actually re-issuing is not itself terminal.

    Fix round 2: when this flag is set, the current_revision_id repoint
    below is ALSO scoped away from published/cancelled rows -- a published
    row must keep pointing at the exact revision that was published
    (Task 2's invariant: `current_revision_id` is what `current_revision`,
    dispatch checks and the removals/admin views all read back as "what
    this row shows"). Repointing it to a revision that dropped a member it
    already went out with would be a lie about what is actually live. The
    published/cancelled sibling's own status, external_post_id and
    image_key are separate `publishing_queue` columns this function never
    touches regardless. `edit_caption`/`approve_card` never pass this flag,
    so admin-driven edits keep the original whole-request guard AND the
    original whole-request repoint.

    Wave 2 Task 5 (Task 3 re-review): every row this call re-points also
    loses its `image_key`, `image_url` and `image_sha256`, and each old key
    no published sibling still references is queued for deletion. A caption
    edit on a fully rendered request therefore leaves BOTH platform rows
    un-rendered, and `complete_render_if_ready` cannot pass on one fresh
    upload paired with the other platform's stale image."""
    statuses = _statuses(tx, request_key)
    if statuses & set(IN_FLIGHT):
        raise ValueError('in_flight')
    if statuses & set(TERMINAL) and not ignore_terminal_siblings:
        raise ValueError('terminal')
    # Wave 2 Task 5 (Task 3 re-review): the artwork a row carries was
    # rendered for the revision it is being moved OFF. Leaving it in place
    # would let `complete_render_if_ready` pass on a single fresh upload,
    # pairing one newly rendered platform with the other platform's stale
    # image -- a card showing the old caption going out beside the new one.
    # So every row this call re-points is un-rendered here, and the old keys
    # are queued for deletion. A key a published sibling still points at is
    # left alone: that object is what is actually live.
    #
    # One scope serves both branches below. With `ignore_terminal_siblings`
    # set, the repoint is already scoped away from published/cancelled rows,
    # so those are not re-pointed and must not be un-rendered. Without it, the
    # terminal guard above has already refused the whole call if any such row
    # exists, so the scope simply matches every row of the request_key --
    # which is exactly the set the default repoint touches.
    old_keys = [r['image_key'] for r in tx.execute(
        _Q_OLD_KEYS, dict(rk=request_key)).fetchall()]
    next_rev = tx.execute(
        "SELECT COALESCE(MAX(revision), 0) + 1 AS n FROM spotlight_revision WHERE request_key = %(rk)s",
        dict(rk=request_key)).fetchone()['n']
    row = tx.execute(
        """INSERT INTO spotlight_revision
               (request_key, revision, caption, photo_uuid, layout_version, channels, participants, created_by)
           VALUES (%(rk)s, %(rev)s, %(cap)s, %(photo)s::uuid, %(lv)s, %(ch)s, %(part)s::jsonb, %(by)s)
           RETURNING id""",
        dict(rk=request_key, rev=next_rev, cap=caption, photo=photo_uuid, lv=layout_version,
             ch=channels, part=json.dumps(participants or []), by=created_by)).fetchone()
    revision_id = row['id']
    # Every row of the request_key shares one current revision; a roundup and
    # a welcome card never share a request_key, so this UPDATE never crosses
    # kinds. Scoped away from published/cancelled rows only when
    # ignore_terminal_siblings is set (see docstring); the default path
    # keeps the whole-key repoint exactly as it always has.
    if ignore_terminal_siblings:
        tx.execute(
            """UPDATE publishing_queue SET current_revision_id = %(rid)s, updated_at = NOW()
                WHERE request_key = %(rk)s AND status NOT IN ('published', 'cancelled')""",
            dict(rid=revision_id, rk=request_key))
    else:
        tx.execute(
            "UPDATE publishing_queue SET current_revision_id = %(rid)s, updated_at = NOW() WHERE request_key = %(rk)s",
            dict(rid=revision_id, rk=request_key))
    # Un-render the re-pointed rows and queue their old artwork for deletion
    # (see the comment above the `old_keys` read). Enqueueing is a plain
    # database write, so it belongs in this transaction: if the caller rolls
    # back, the revision and the cleanup jobs go together.
    tx.execute(_Q_UNRENDER, dict(rk=request_key))
    for key in old_keys:
        enqueue_asset_delete(tx, key)
    return revision_id


def current_revision(tx, request_key: str) -> Optional[dict]:
    return tx.execute(
        """SELECT r.id, r.request_key, r.revision, r.caption, r.photo_uuid::text AS photo_uuid,
                  r.layout_version, r.channels, r.participants, r.asset_hash,
                  r.image_key, r.image_url, r.created_by, r.created_at
             FROM spotlight_revision r
             JOIN publishing_queue q ON q.current_revision_id = r.id
            WHERE q.request_key = %(rk)s
            LIMIT 1""",
        dict(rk=request_key)).fetchone()


def attach_render(tx, revision_id: int, asset_hash: str, image_key: str, image_url: str) -> None:
    row = tx.execute("SELECT asset_hash FROM spotlight_revision WHERE id = %(id)s",
                     dict(id=revision_id)).fetchone()
    if not row:
        raise ValueError('not_found')
    if row['asset_hash'] is not None:
        raise ValueError('already_rendered')
    tx.execute(
        """UPDATE spotlight_revision SET asset_hash = %(h)s, image_key = %(k)s, image_url = %(u)s
            WHERE id = %(id)s""",
        dict(h=asset_hash, k=image_key, u=image_url, id=revision_id))


def record_consent(tx, revision_id: int, person_id: int, role: str, nonce: Optional[str] = None) -> bool:
    cur = tx.execute(
        """INSERT INTO spotlight_revision_consent (revision_id, person_id, role, nonce)
           VALUES (%(rid)s, %(pid)s, %(role)s, %(nonce)s)
           ON CONFLICT (revision_id, person_id, role) DO NOTHING""",
        dict(rid=revision_id, pid=person_id, role=role, nonce=nonce))
    return cur.rowcount > 0


def consent_complete(tx, revision_id: int) -> bool:
    rev = tx.execute("SELECT request_key, participants FROM spotlight_revision WHERE id = %(id)s",
                      dict(id=revision_id)).fetchone()
    if not rev:
        return False
    subject = tx.execute(
        """SELECT subject_person_id FROM publishing_queue
            WHERE request_key = %(rk)s AND subject_person_id IS NOT NULL LIMIT 1""",
        dict(rk=rev['request_key'])).fetchone()
    if subject:
        return tx.execute(
            """SELECT 1 FROM spotlight_revision_consent
                WHERE revision_id = %(id)s AND person_id = %(pid)s AND role = 'subject'""",
            dict(id=revision_id, pid=subject['subject_person_id'])).fetchone() is not None
    # Roundup (no subject): every tiled participant must have consented.
    # Count-only (empty participants) is complete by definition.
    participants = rev['participants'] or []
    if not participants:
        return True
    consented = {r['person_id'] for r in tx.execute(
        "SELECT person_id FROM spotlight_revision_consent WHERE revision_id = %(id)s AND role = 'participant'",
        dict(id=revision_id)).fetchall()}
    return all(p['person_id'] in consented for p in participants)


def edit_caption(tx, request_key: str, caption: str, created_by: str) -> int:
    cur = current_revision(tx, request_key)
    caption_text = caption
    # Phase B's caption route logic, moved here: the /s/ campaign link is
    # minted once per request at create_candidate time and must survive a
    # hand-edited caption, or the post's own CTA (and its click/signup
    # counting) is silently lost.
    link = tx.execute("SELECT key FROM campaign_link WHERE kind = %(k)s LIMIT 1",
                      dict(k=f'post:{request_key}')).fetchone()
    if link:
        url = f"{WEB_BASE_URL.rstrip('/')}/s/{link['key']}"
        if url not in caption_text:
            caption_text = f"{caption_text.rstrip()} {url}"
    new_id = create_revision(
        tx, request_key, caption=caption_text,
        photo_uuid=cur['photo_uuid'] if cur else None,
        participants=(cur['participants'] if cur else None) or [],
        channels=(cur['channels'] if cur else None) or [],
        layout_version=cur['layout_version'] if cur else 'v1',
        created_by=created_by)
    tx.execute("UPDATE publishing_queue SET caption = %(c)s, updated_at = NOW() WHERE request_key = %(rk)s",
               dict(c=caption_text, rk=request_key))
    return new_id


def approve_card(tx, request_key: str, person_id: int, photo_uuid: Optional[str], *,
                  nonce: Optional[str] = None) -> str:
    from service.spotlight.queue import settings as _settings, set_status  # lazy: see module docstring
    if _settings(tx).get('approvals_enabled') != 'true':
        raise ValueError('approvals_disabled')
    subject = tx.execute(
        "SELECT subject_person_id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1",
        dict(rk=request_key)).fetchone()
    if not subject or subject['subject_person_id'] != person_id:
        raise ValueError('not_subject')
    rev = current_revision(tx, request_key)
    if not rev:
        raise ValueError('not_found')

    if photo_uuid and photo_uuid != rev['photo_uuid']:
        owned = tx.execute(
            """SELECT 1 FROM photo WHERE uuid::text = %(u)s AND person_id = %(pid)s
                AND moderation_status = 'approved'""",
            dict(u=photo_uuid, pid=person_id)).fetchone()
        if not owned:
            raise ValueError('photo_not_owned')
        # A different photo means a card the member hasn't seen rendered yet
        # -- a fresh, un-rendered revision, and no consent recorded against
        # it. The caller re-renders and the member approves again.
        create_revision(
            tx, request_key, caption=rev['caption'], photo_uuid=photo_uuid,
            participants=rev['participants'] or [], channels=rev['channels'] or [],
            layout_version=rev['layout_version'], created_by=f'member:{person_id}')
        return 'new_revision'

    if rev['asset_hash'] is None:
        raise ValueError('preview_unavailable')

    inserted = record_consent(tx, rev['id'], person_id, 'subject', nonce=nonce)
    if not inserted:
        return 'already'
    if consent_complete(tx, rev['id']):
        # Through the transition table (TRANSITIONS['awaiting_member'] now
        # allows 'review'), not a raw UPDATE, so this stays subject to the
        # same rules every other status change is.
        for row in tx.execute(
                "SELECT id FROM publishing_queue WHERE request_key = %(rk)s AND status = 'awaiting_member'",
                dict(rk=request_key)).fetchall():
            set_status(tx, row['id'], 'review')
    return 'approved'
