"""Publishing queue operations (spec 3.6). Every function runs inside the caller's api_tx; never open one here."""
from __future__ import annotations
import hmac
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
    'failed': {'scheduled', 'cancelled'},
    'awaiting_member': {'awaiting_render', 'cancelled', 'review'},
    'awaiting_render': {'review', 'cancelled'},
}
# `processing` is deliberately NOT a key here (Wave 1 F04): the only ways out
# of it are `claim_spotlight_posts`' own SQL (never reached this table) and
# `record_receipt` below, which binds the exit to the lease that produced it.
# `set_status` therefore can no longer move a `processing` row anywhere --
# it stays available for the operator moves that never touch `processing`
# (review -> scheduled, failed -> scheduled, etc).
MAX_ATTEMPTS = 3
# Outcomes a publish attempt can report back through `record_receipt`. Kept
# distinct from `publishing_queue.status`: 'review' as an outcome always
# lands the row in status 'review' with delivery_state reset to 'none' (an
# operator decision, e.g. "ineligible now"), while 'delivery_unknown' also
# lands in status 'review' but keeps delivery_state 'delivery_unknown' so the
# two park-in-review reasons stay distinguishable on the row.
OUTCOMES = ('published', 'failed', 'delivery_unknown', 'review')
OUTCOME_DELIVERY_STATE = {
    'published': 'published',
    'failed': 'failed',
    'delivery_unknown': 'delivery_unknown',
    'review': 'none',
}
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
    # stay empty (count-only) unless roundup_tiles_enabled is on, matching
    # roundup_snapshot's own gate on the same setting (Task 8) -- the
    # setting is seeded false, so this branch only fires once it is turned
    # on. `post_growth_spotlight_roundup` replaces this revision with a
    # richer one (first_name/photo_url per participant) whenever tiles are
    # non-empty, so this initial revision is the count-only steady state.
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


_Q_RECEIPT_ROW = """
    SELECT status, lease_token, delivery_state, external_post_id, cancellation_requested_at,
           subject_person_id, platform, request_key
      FROM publishing_queue WHERE id = %(id)s FOR UPDATE
"""


def _file_late_removal_task(tx, queue_id, row: dict, external_post_id: Optional[str]) -> None:
    """A `published` receipt landing after the member withdrew (row 5 rule):
    the removal task has to be filed the moment the post is known to be
    live, not left for the next withdrawal (there may not be one).

    `record_receipt` only knows THAT `cancellation_requested_at` is set, not
    WHO withdrew: a roundup row has no subject, and the withdrawing member's
    own `withdraw_member` call is what names them, on whichever rows it
    reached directly (their own card, or an already-published sibling row).
    This is just the one row that was still in flight -- for a roundup, it
    files with `person_id NULL`, exactly like `row['subject_person_id']`
    already is for that kind. Lazy import mirrors `cancel_for_member`'s:
    `withdrawal.py` does not import this module, so there is no real cycle,
    but the pattern stays consistent with the rest of the file.
    """
    from service.spotlight.withdrawal import _file_removal_tasks
    task_row = dict(id=queue_id, platform=row['platform'], external_post_id=external_post_id,
                     request_key=row['request_key'])
    _file_removal_tasks(tx, [task_row], row['subject_person_id'])


def record_receipt(tx, queue_id, lease_token: Optional[str], outcome: str, *,
                    external_post_id: Optional[str] = None, post_url: Optional[str] = None,
                    error: Optional[str] = None) -> str:
    """The worker's report of what happened to one lease's dispatch attempt
    (Wave 1 F04). Bound to the exact lease `claim_spotlight_posts` handed
    out, so a receipt for an attempt nobody currently holds (a stale lease,
    a reused lease, no lease at all) is refused rather than trusted.

    Delivery state is deliberately separate from withdrawal: a member can
    withdraw while a row is `processing`, and the lease holder must still be
    able to report what actually happened on the platform -- `withdraw_member`
    only stamps `cancellation_requested_at` on such a row (Task 4) and never
    touches its status, so this function is the only thing that can move it
    out of `processing`. See module docstring / TRANSITIONS above: `set_status`
    can no longer do so.

    Returns 'recorded' or 'already'. Raises ValueError with one of:
    not_found, lease_required, lease_mismatch, not_processing, bad_outcome,
    external_id_required.
    """
    if outcome not in OUTCOMES:
        raise ValueError('bad_outcome')
    # A truthy non-string (an int, say, from a loosely-typed request body)
    # must never reach `hmac.compare_digest` below, which raises TypeError
    # on anything but two `str` (or two `bytes`) -- fail closed here instead.
    if not isinstance(lease_token, str) or not lease_token:
        raise ValueError('lease_required')
    row = tx.execute(_Q_RECEIPT_ROW, dict(id=queue_id)).fetchone()
    if not row:
        raise ValueError('not_found')
    if not row['lease_token'] or not hmac.compare_digest(row['lease_token'], lease_token):
        raise ValueError('lease_mismatch')
    if row['status'] != 'processing':
        duplicate = (
            (outcome == 'published' and row['status'] == 'published'
             and row['external_post_id'] == external_post_id)
            or (outcome == 'failed' and row['status'] == 'failed')
            or (outcome == 'delivery_unknown' and row['status'] == 'review'
                and row['delivery_state'] == 'delivery_unknown')
            # A `review` receipt is a duplicate whenever the row is already
            # parked in review, regardless of its delivery_state: an
            # operator-parked row (an ineligible check, a lease-expiry reap,
            # or an earlier delivery_unknown) is not reset by a repeat
            # `review` receipt -- there is nothing left for a second one to
            # change.
            or (outcome == 'review' and row['status'] == 'review')
        )
        if duplicate:
            return 'already'
        raise ValueError('not_processing')

    if outcome == 'published':
        if not external_post_id:
            raise ValueError('external_id_required')
        tx.execute(
            """UPDATE publishing_queue SET status = 'published', delivery_state = 'published',
                      external_post_id = %(ext)s, post_url = %(url)s, error = NULL,
                      lease_until = NULL, updated_at = NOW()
                WHERE id = %(id)s""",
            dict(ext=external_post_id, url=post_url, id=queue_id))
        if row['cancellation_requested_at'] is not None:
            _file_late_removal_task(tx, queue_id, row, external_post_id)
    elif outcome == 'failed':
        tx.execute(
            """UPDATE publishing_queue SET status = 'failed', delivery_state = 'failed',
                      error = %(err)s, lease_until = NULL, updated_at = NOW()
                WHERE id = %(id)s""",
            dict(err=error, id=queue_id))
    elif outcome == 'delivery_unknown':
        tx.execute(
            """UPDATE publishing_queue SET status = 'review', delivery_state = 'delivery_unknown',
                      error = %(err)s, lease_until = NULL, updated_at = NOW()
                WHERE id = %(id)s""",
            dict(err=error, id=queue_id))
    else:  # 'review'
        tx.execute(
            """UPDATE publishing_queue SET status = 'review', delivery_state = 'none',
                      error = %(err)s, lease_until = NULL, updated_at = NOW()
                WHERE id = %(id)s""",
            dict(err=error, id=queue_id))
    return 'recorded'


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
