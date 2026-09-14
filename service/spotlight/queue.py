"""Publishing queue operations (spec 3.6). Every function runs inside the caller's api_tx; never open one here."""
from __future__ import annotations
import uuid
from typing import Optional
from service.campaigns import make_campaign_link
from service.config import WEB_BASE_URL
from service.spotlight.eligibility import eligibility, primary_photo_uuid
from service.spotlight.revisions import create_revision
from service.spotlight.roundup import roundup_snapshot

KINDS = ('welcome', 'roundup', 'member_of_week', 'highlight')
PLATFORMS = ('facebook', 'instagram')
TRANSITIONS = {
    'review': {'scheduled', 'cancelled'},
    'scheduled': {'cancelled', 'processing'},
    'processing': {'published', 'failed', 'review'},
    'failed': {'scheduled', 'cancelled'},
    'awaiting_member': {'awaiting_render', 'cancelled', 'review'},
    'awaiting_render': {'review', 'cancelled'},
}
MAX_ATTEMPTS = 3
# Wave 1 (migration 0044) seeded five more boolean flags alongside the
# original three; `approve_card`, `edit_caption` and the render tick all
# gate on the new ones, so they must be settable the same way.
_SETTING_KEYS = ('scheduler_enabled', 'auto_welcome', 'auto_roundup',
                 'approvals_enabled', 'roundup_tiles_enabled', 'invites_enabled',
                 'publication_enabled', 'external_access_enabled')
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

    # Revision 1 (Wave 1, F01): every request's content starts life as an
    # immutable revision, and every queue row of the key points at it -- a
    # welcome/member_of_week card's chosen photo is its subject's primary
    # approved photo; a roundup carries no photo of its own. Participants
    # stay empty (count-only) unless roundup_tiles_enabled is on, which
    # Task 8 formalises properly -- the setting is seeded false, so that
    # branch is dormant today.
    photo_uuid = primary_photo_uuid(tx, subject_person_id) if subject_person_id is not None else None
    participants: list = []
    if kind == 'roundup' and settings(tx).get('roundup_tiles_enabled') == 'true':
        participants = [{'person_id': tile['person_id'], 'photo_uuid': None}
                         for tile in roundup_snapshot(tx)['tiles']]
    create_revision(tx, rk, caption=caption, photo_uuid=photo_uuid, participants=participants,
                    channels=list(platforms), created_by=created_by)
    return rk


def expire_member_approvals(tx, days: int = 7) -> int:
    cur = tx.execute(
        """UPDATE publishing_queue SET status = 'cancelled', error = 'approval_expired', updated_at = NOW()
            WHERE status = 'awaiting_member' AND created_at < NOW() - make_interval(days => %(d)s)""",
        dict(d=days))
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
    """Thin wrapper kept for existing callers. The real logic -- filing
    removal tasks for published rows, leaving mid-publish rows alone,
    re-issuing roundups that can drop the member, cancelling the rest,
    bumping the consent epoch and invalidating nonces -- now lives in
    `withdraw_member` (service/spotlight/withdrawal.py, Wave 1 F03), the
    single exit point every lifecycle event goes through. Imported lazily
    to avoid a module-load cycle (withdrawal.py sits below queue.py in the
    same package)."""
    from service.spotlight.withdrawal import withdraw_member
    return withdraw_member(tx, person_id, reason)['cancelled']


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
