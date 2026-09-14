"""Publishing queue operations (spec 3.6). Every function runs inside the caller's api_tx; never open one here."""
from __future__ import annotations
import uuid
from typing import Optional
from service.spotlight.eligibility import eligibility

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


def cancel_for_member(tx, person_id: int, reason: str) -> int:
    published = tx.execute(
        "SELECT id, platform, external_post_id FROM publishing_queue WHERE subject_person_id = %(pid)s AND status = 'published'",
        dict(pid=person_id)).fetchall()
    for r in published:
        tx.execute(
            """INSERT INTO spotlight_removal_task (queue_id, platform, external_post_id, reason)
               VALUES (%(q)s, %(pl)s, %(ext)s, %(reason)s)""",
            dict(q=r['id'], pl=r['platform'], ext=r['external_post_id'],
                 reason='delete_via_api' if r['platform'] == 'facebook' else 'manual_instagram'))
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'cancelled', error = %(reason)s, updated_at = NOW()
            WHERE subject_person_id = %(pid)s AND status NOT IN ('published', 'cancelled')""",
        dict(pid=person_id, reason=reason))
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
