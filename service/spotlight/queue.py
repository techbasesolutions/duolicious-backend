"""Publishing queue operations (spec 3.6). Every function runs inside the caller's api_tx; never open one here."""
from __future__ import annotations
import uuid
from typing import Optional
from service.campaigns import make_campaign_link
from service.config import WEB_BASE_URL
from service.spotlight.eligibility import eligibility
from service.spotlight.storage import delete_images

KINDS = ('welcome', 'roundup', 'member_of_week', 'highlight')
PLATFORMS = ('facebook', 'instagram')
TRANSITIONS = {
    'review': {'scheduled', 'cancelled'},
    'scheduled': {'cancelled', 'processing'},
    'processing': {'published', 'failed', 'review'},
    'failed': {'scheduled', 'cancelled'},
    'awaiting_member': {'awaiting_render', 'cancelled'},
    'awaiting_render': {'review', 'cancelled'},
}
MAX_ATTEMPTS = 3
_SETTING_KEYS = ('scheduler_enabled', 'auto_welcome', 'auto_roundup')
# Keys whose value is not a 'true'/'false' flag. The page-token health probe
# writes an ISO timestamp and a validity flag here, so these two accept any
# string value; every other key stays a strict boolean.
_FREE_KEYS = ('token_expires_at', 'token_valid')


def create_candidate(tx, *, kind: str, subject_person_id: Optional[int], caption: str, created_by: str, platforms=PLATFORMS) -> str:
    if kind not in KINDS:
        raise ValueError('bad_kind')
    if kind == 'roundup':
        if subject_person_id is not None:
            raise ValueError('roundup_has_no_subject')
        status = 'awaiting_render'
    else:
        if subject_person_id is None:
            raise ValueError('subject_required')
        ok, reason = eligibility(tx, subject_person_id)
        if not ok:
            raise ValueError(reason)
        status = 'awaiting_member'
    rk = uuid.uuid4().hex
    # Spec 3.4: every published card carries a measurable CTA, so the link is
    # minted here, once per request, and appended to the caption both platform
    # rows share. Minting it at creation (rather than in each caption builder)
    # means a hand-written caption from the admin surface gets one too, and
    # the kind 'post:<request_key>' is what `post_stats` and the queue view
    # count clicks and sign-ups against.
    link = make_campaign_link(tx, f'post:{rk}', f"{WEB_BASE_URL}/", None)
    caption = f"{caption} {link}"
    for platform in platforms:
        tx.execute(
            """INSERT INTO publishing_queue (request_key, kind, subject_person_id, platform, caption, status, created_by)
               VALUES (%(rk)s, %(kind)s, %(pid)s, %(pl)s, %(cap)s, %(st)s, %(by)s)""",
            dict(rk=rk, kind=kind, pid=subject_person_id, pl=platform, cap=caption, st=status, by=created_by))
    return rk


def set_member_approval(tx, request_key: str, photo_uuid: str) -> int:
    owner = tx.execute("SELECT subject_person_id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=request_key)).fetchone()
    if not owner or owner['subject_person_id'] is None:
        raise ValueError('no_subject')
    owned = tx.execute("SELECT 1 FROM photo WHERE uuid::text = %(u)s AND person_id = %(pid)s AND moderation_status = 'approved'",
                       dict(u=photo_uuid, pid=owner['subject_person_id'])).fetchone()
    if not owned:
        raise ValueError('photo_not_owned')
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'awaiting_render', member_approved_at = NOW(),
                  approved_photo_uuid = %(u)s::uuid, updated_at = NOW()
            WHERE request_key = %(rk)s AND status = 'awaiting_member'""",
        dict(u=photo_uuid, rk=request_key))
    return cur.rowcount


def expire_member_approvals(tx, days: int = 7) -> int:
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'cancelled', error = 'approval_expired', updated_at = NOW()
            WHERE status = 'awaiting_member' AND created_at < NOW() - make_interval(days => %(d)s)""",
        dict(d=days))
    return cur.rowcount


def attach_image(tx, request_key: str, image_key: str, image_url: str) -> int:
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'review', image_key = %(k)s, image_url = %(u)s, updated_at = NOW()
            WHERE request_key = %(rk)s AND status = 'awaiting_render'""",
        dict(k=image_key, u=image_url, rk=request_key))
    return cur.rowcount


def set_status(tx, queue_id: str, status: str, *, external_post_id: Optional[str] = None, error: Optional[str] = None) -> None:
    row = tx.execute("SELECT status, attempts FROM publishing_queue WHERE id = %(id)s FOR UPDATE", dict(id=queue_id)).fetchone()
    if not row:
        raise ValueError('not_found')
    allowed = TRANSITIONS.get(row['status'], set())
    if status not in allowed:
        raise ValueError('bad_transition')
    if row['status'] == 'failed' and status == 'scheduled' and row['attempts'] >= MAX_ATTEMPTS:
        raise ValueError('bad_transition')
    tx.execute(
        """UPDATE publishing_queue SET status = %(st)s,
                  external_post_id = COALESCE(%(ext)s, external_post_id),
                  error = %(err)s, lease_until = NULL, updated_at = NOW()
            WHERE id = %(id)s""",
        dict(st=status, ext=external_post_id, err=error, id=queue_id))


# Containment against the stored snapshot: a tile object carries first_name
# and photo_url as well, so `@>` with just the person_id matches the whole
# tile without having to reproduce the rest of it.
_TILE_MATCH = """
    kind = 'roundup'
      AND payload->'tiles' @> jsonb_build_array(jsonb_build_object('person_id', %(pid)s))
"""

_Q_TILE_IMAGE_KEYS = f"""
    SELECT image_key FROM publishing_queue
     WHERE {_TILE_MATCH}
       AND image_key IS NOT NULL
       AND status NOT IN ('published', 'cancelled')
"""

_Q_CANCEL_TILE_ROWS = f"""
    UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s, updated_at = NOW()
     WHERE {_TILE_MATCH}
       AND status NOT IN ('published', 'cancelled')
"""

_Q_PUBLISHED_SUBJECT_ROWS = """
    SELECT id, platform, external_post_id FROM publishing_queue
     WHERE subject_person_id = %(pid)s AND status = 'published'
"""

_Q_PUBLISHED_TILE_ROWS = f"""
    SELECT id, platform, external_post_id FROM publishing_queue
     WHERE {_TILE_MATCH}
       AND status = 'published'
"""


def _file_removal_tasks(tx, rows) -> int:
    """One open task per published platform row. A Facebook post can be
    deleted through the Graph API; Instagram has no delete endpoint for
    published media, so that one is flagged for a human instead.

    Those two reason strings are the contract the admin worker and the
    removals list read, so a task filed for a roundup tile uses exactly the
    same pair as one filed for a card's subject. Nothing downstream has to
    know why the task exists in order to action it.

    Guarded with NOT EXISTS on an already-open task for the same queue_id:
    a roundup's tile snapshot names up to four members, so several of them
    opting out one after another all match the same published queue rows in
    `_Q_PUBLISHED_TILE_ROWS`, and without this guard each opt-out would file
    its own duplicate task for the same post."""
    n = 0
    for r in rows:
        cur = tx.execute(
            """INSERT INTO spotlight_removal_task (queue_id, platform, external_post_id, reason)
               SELECT %(q)s, %(pl)s, %(ext)s, %(reason)s
                WHERE NOT EXISTS (SELECT 1 FROM spotlight_removal_task t
                                   WHERE t.queue_id = %(q)s AND t.done_at IS NULL)""",
            dict(q=r['id'], pl=r['platform'], ext=r['external_post_id'],
                 reason='delete_via_api' if r['platform'] == 'facebook' else 'manual_instagram'))
        n += cur.rowcount
    return n


def cancel_for_member(tx, person_id: int, reason: str) -> int:
    _file_removal_tasks(tx, tx.execute(_Q_PUBLISHED_SUBJECT_ROWS, dict(pid=person_id)).fetchall())
    # A published roundup that tiles this member shows their photo exactly as a
    # published card of their own does, so it earns the same removal task. The
    # row's status is deliberately left at 'published': that is still the truth
    # until the platform post is actually gone, and the task is what records
    # that it has to go.
    _file_removal_tasks(tx, tx.execute(_Q_PUBLISHED_TILE_ROWS, dict(pid=person_id)).fetchall())
    # Published rows keep their card until the retention sweep or a removal
    # task marks the platform post done -- only non-published rows' images
    # are deleted here, since those never got (and now never will get) a
    # public post to point at.
    keys = [r['image_key'] for r in tx.execute(
        """SELECT image_key FROM publishing_queue
            WHERE subject_person_id = %(pid)s AND image_key IS NOT NULL
              AND status NOT IN ('published')""",
        dict(pid=person_id)).fetchall()]
    # rowcount is read straight away: `tx.execute` hands back the connection's
    # one cursor, so the next statement would overwrite it.
    cancelled = tx.execute(
        """UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s, updated_at = NOW()
            WHERE subject_person_id = %(pid)s AND status NOT IN ('published', 'cancelled')""",
        dict(pid=person_id, reason=reason)).rowcount
    # A roundup row carries no subject_person_id at all, so the sweep above
    # cannot see it -- but its stored tile snapshot names (and shows the photo
    # of) up to four members. Withdrawn consent has to reach those rows too,
    # or a member who opted out is still published inside someone else's card.
    # Published roundups keep their status here, the same way published subject
    # rows do: they were handled at the top, by a removal task.
    tile_keys = [r['image_key'] for r in tx.execute(_Q_TILE_IMAGE_KEYS, dict(pid=person_id)).fetchall()]
    tiled = tx.execute(_Q_CANCEL_TILE_ROWS, dict(pid=person_id, reason=f'tile_member_{reason}')).rowcount
    # Storage is best-effort and outside the transaction's success/failure:
    # a Spaces error here must never roll back the cancellations above.
    delete_images(keys + tile_keys)
    return cancelled + tiled


def reap_expired_leases(tx) -> int:
    """An interrupted publish (worker crash, deploy, network partition) may
    have actually succeeded on the platform before the lease expired, so
    this never auto-retries (never moves back to 'scheduled') -- it lands in
    'review' for a human to check (spec 5)."""
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'review', error = 'lease_expired',
                  lease_until = NULL, updated_at = NOW()
            WHERE status = 'processing' AND lease_until < NOW()""")
    return cur.rowcount


def settings(tx) -> dict:
    return {r['key']: r['value'] for r in tx.execute("SELECT key, value FROM spotlight_setting").fetchall()}


def set_setting(tx, key: str, value: str) -> None:
    if key in _FREE_KEYS:
        if value is None:
            value = ''
        if not isinstance(value, str):
            raise ValueError('bad_setting')
    elif key not in _SETTING_KEYS or value not in ('true', 'false'):
        raise ValueError('bad_setting')
    tx.execute("INSERT INTO spotlight_setting (key, value, updated_at) VALUES (%(k)s, %(v)s, NOW()) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
               dict(k=key, v=value))


def stamp_featured(tx, person_id: int) -> None:
    tx.execute("UPDATE person SET spotlight_last_featured_at = NOW() WHERE id = %(pid)s", dict(pid=person_id))
