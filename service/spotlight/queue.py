"""Publishing queue operations (spec 3.6). Every function runs inside the caller's api_tx; never open one here."""
from __future__ import annotations
import hmac
import re
import uuid
from typing import Optional
from service.campaigns import PLATFORMS, make_campaign_link, with_platform
from service.config import WEB_BASE_URL
from service.spotlight.eligibility import eligibility, primary_photo_uuid
from service.spotlight.revisions import create_revision

KINDS = ('welcome', 'roundup', 'member_of_week', 'highlight')
# PLATFORMS lives in service.campaigns (Wave 3c task 1): this module already
# imports from there at load time (make_campaign_link, with_platform), so
# importing the constant the same way carries no cycle. Re-exported here
# unchanged -- service.api.admin.spotlight_routes and others still import it
# as `service.spotlight.queue.PLATFORMS`.
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
# Task 9 (F12): the one `scheduler_enabled` switch gated publishing and
# removals together while the tick ran regardless, and `auto_welcome`/
# `auto_roundup` were stored and never read. Migration 0045 deletes all
# three rows; the five Wave 1 keys (migration 0044) are the whole surface
# now -- `invites_enabled` (candidate creation and E4), `publication_enabled`
# (claiming and publishing) and `external_access_enabled` (the emergency
# stop for every outbound platform call, removals included) join
# `approvals_enabled` and `roundup_tiles_enabled`.
SETTING_KEYS = ('approvals_enabled', 'roundup_tiles_enabled', 'invites_enabled',
                'publication_enabled', 'external_access_enabled')
# Keys whose value is not a 'true'/'false' flag. The page-token health probe
# writes an ISO timestamp and a validity flag here, so these two accept any
# string value; every other key stays a strict boolean.
FREE_KEYS = ('token_expires_at', 'token_valid')

# A caller-supplied business key (Task 9): letters, digits, and the three
# separators a key like `roundup:2026-W38` needs (the ISO week number's own
# leading `W`, from the route's own week_key, is uppercase). Anything else
# -- spaces, punctuation -- is refused rather than silently slugified.
_REQUEST_KEY_RE = re.compile(r'^[a-zA-Z0-9:_-]{1,64}$')


def create_candidate(tx, *, kind: str, subject_person_id: Optional[int], caption: str, created_by: str,
                     platforms=PLATFORMS, request_key: Optional[str] = None) -> str:
    if kind not in KINDS:
        raise ValueError('bad_kind')
    if request_key is not None:
        if not _REQUEST_KEY_RE.match(request_key):
            raise ValueError('bad_request_key')
        # Converge: a caller racing its own business key (the weekly roundup
        # cron firing twice) gets the existing rows back untouched rather
        # than a second copy. A key collision across kinds (someone else's
        # business key, reused by accident) is not a convergence -- it is a
        # bad key, refused the same way an invalid pattern is.
        existing = tx.execute("SELECT kind FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1",
                              dict(rk=request_key)).fetchone()
        if existing:
            if existing['kind'] != kind:
                raise ValueError('bad_request_key')
            return request_key
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
    rk = request_key or uuid.uuid4().hex
    # Spec 3.4: every published card carries a measurable CTA, so the link is
    # minted here, once per request, and appended to every platform row's
    # caption. Minting it at creation (rather than in each caption builder)
    # means a hand-written caption from the admin surface gets one too, and
    # the kind 'post:<request_key>' is what `post_stats` and the queue view
    # count clicks and sign-ups against.
    #
    # Fix wave I1: the link is one key shared by every platform row, but each
    # row's caption now carries it stamped with that row's own `?p=`
    # (`with_platform`), so a click from the Facebook post and a click from
    # the Instagram post are told apart by `campaign_click.platform`. Before
    # this, one caption string was built before the loop and inserted
    # verbatim into every row, so nothing ever emitted `?p=` and every real
    # click landed under 'unknown'.
    #
    # `base_caption` (the same text with the BARE link) is what the revision
    # records: a revision is one immutable snapshot shared by every row of
    # the request, so the platform-neutral form is the honest thing to store
    # there, and `edit_caption` re-derives the per-row captions from it the
    # same way this does.
    link = make_campaign_link(tx, f'post:{rk}', f"{WEB_BASE_URL}/", None)
    base_caption = f"{caption} {link}"
    for platform in platforms:
        tx.execute(
            """INSERT INTO publishing_queue (request_key, kind, subject_person_id, platform, caption, status, created_by)
               VALUES (%(rk)s, %(kind)s, %(pid)s, %(pl)s, %(cap)s, %(st)s, %(by)s)""",
            dict(rk=rk, kind=kind, pid=subject_person_id, pl=platform,
                 cap=f"{caption} {with_platform(link, platform)}", st=status, by=created_by))

    # Revision 1 (Wave 1, F01): every request's content starts life as an
    # immutable revision, and every queue row of the key points at it -- a
    # welcome/member_of_week card's chosen photo is its subject's primary
    # approved photo; a roundup carries no photo of its own. Participants
    # always start empty here (Task 8 fix round 1, ruling 1): a roundup's
    # tiled participants belong to `post_growth_spotlight_roundup`, which
    # replaces this revision with a richer one (first_name/photo_url per
    # participant) whenever `roundup_tiles_enabled` is on and the snapshot
    # has tiles. `create_candidate` itself never reads that setting or
    # calls `roundup_snapshot` -- revision 1 of every roundup is always the
    # count-only steady state.
    photo_uuid = primary_photo_uuid(tx, subject_person_id) if subject_person_id is not None else None
    create_revision(tx, rk, caption=base_caption, photo_uuid=photo_uuid, participants=[],
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


_Q_LOCKED_ROW = """
    SELECT id, status, lease_token, delivery_state, external_post_id, post_url,
           cancellation_requested_at, subject_person_id, platform, request_key, kind,
           current_revision_id
      FROM publishing_queue WHERE id = %(id)s FOR UPDATE
"""

# A row is recoverable when it left `processing` without anyone being able to
# say what the platform did with it: reaped into `review` after its lease
# expired (delivery_state still 'attempting'), or parked there by a
# `delivery_unknown` receipt. Either way a post may be LIVE, so the row must
# stay reachable by a late definite answer -- from its own lease holder
# (`record_receipt`) or from an operator (`reconcile_published`).
RECOVERABLE_DELIVERY_STATES = ('attempting', 'delivery_unknown')


def lock_queue_row(tx, queue_id):
    """Take the row lock for one queue row and read it back, or None when
    there is no such row.

    `record_receipt` takes this lock itself, as a re-lock: the complete route
    calling it has already taken the wider `lock_request_rows` lock below, so
    this never contends with anything. A caller with no wider lock of its own
    (a direct call from a test, say) still gets the same single-row
    correctness this always gave."""
    return tx.execute(_Q_LOCKED_ROW, dict(id=queue_id)).fetchone()


_Q_REQUEST_ROWS = """
    SELECT id, status, lease_token, delivery_state, external_post_id, post_url,
           cancellation_requested_at, subject_person_id, platform, request_key, kind,
           current_revision_id
      FROM publishing_queue
     WHERE request_key = (SELECT request_key FROM publishing_queue WHERE id = %(id)s)
     ORDER BY id
     FOR UPDATE
"""


def lock_request_rows(tx, queue_id):
    """Lock every row of the request_key the given queue row belongs to, in
    id order, and return them all.

    Residual fix (fix wave item 1): the complete and reconcile routes used to
    take `lock_queue_row`'s single-row lock, then later reach
    `is_first_confirmation` (service/spotlight/occurrence.py), whose own
    `SELECT ... FOR UPDATE` spans every row of the request key. Two sibling
    platform rows completing at the same moment could each hold their own
    row's lock and then block waiting for the other's there -- a genuine
    deadlock. Taking the whole request's lock, in this same id order, before
    either route reads anything off its own row removes the interleaving
    entirely: whichever completion arrives first locks both rows and runs to
    commit before the second can acquire anything, so the second simply
    queues behind the first rather than contending row by row.
    `is_first_confirmation`'s later `FOR UPDATE` over the same rows is then a
    re-lock by the same transaction, which Postgres grants for free.

    Returns [] when there is no such row (queue_id not found): the subquery
    yields NULL and the outer WHERE then matches nothing, so the caller gets
    the same "not found" shape `lock_queue_row`'s None used to give."""
    return tx.execute(_Q_REQUEST_ROWS, dict(id=queue_id)).fetchall()


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


def _file_investigate_task(tx, queue_id, row: dict) -> None:
    """A row that already carries `cancellation_requested_at` just moved INTO
    an unresolved delivery state (residual fix, item 2): `record_receipt`'s
    own `delivery_unknown` outcome, or `reap_expired_leases` reaping it out of
    `processing` on an expired lease. Either way its lease holder is gone and
    nobody is coming back to it on their own -- exactly the situation
    `withdraw_member` already hands an `investigate` task for when the row is
    ALREADY in that state at withdrawal time (service/spotlight/withdrawal.py
    step 3). This is the other direction: the withdrawal came first, stamping
    the row while it was still `processing` (no task filed then -- its lease
    holder was still expected to report back), and only now does the row
    reach the state that needed one.

    Goes through the same `_file_removal_tasks` helper withdrawal uses, so
    the open-task guard (never two open tasks on one row) and the in-place
    upgrade (a later `published` receipt turns this same task into a real
    `delete_via_api`/`manual_instagram` order) both still apply."""
    from service.spotlight.withdrawal import _file_removal_tasks
    task_row = dict(id=queue_id, platform=row['platform'], external_post_id=None,
                     request_key=row['request_key'])
    _file_removal_tasks(tx, [task_row], row['subject_person_id'], reason='investigate')


def _mark_published(tx, queue_id, row: dict, external_post_id: str, post_url: Optional[str]) -> None:
    """The one place a queue row becomes `published`. Shared by the lease
    holder's own receipt and the operator's reconcile, so both leave exactly
    the same row behind -- including the removal task a withdrawal already
    stamped on it."""
    tx.execute(
        """UPDATE publishing_queue SET status = 'published', delivery_state = 'published',
                  external_post_id = %(ext)s, post_url = %(url)s, error = NULL,
                  lease_until = NULL, updated_at = NOW()
            WHERE id = %(id)s""",
        dict(ext=external_post_id, url=post_url, id=queue_id))
    if row['cancellation_requested_at'] is not None:
        _file_late_removal_task(tx, queue_id, row, external_post_id)


def reconcile_published(tx, row: dict, external_post_id: str, post_url: Optional[str] = None) -> dict:
    """The operator's decision that a row parked in `review` with an
    unresolved delivery is in fact live on the platform (spec 5). No lease is
    required: the lease that produced the row is gone by definition, which is
    exactly why a human has to answer for it.

    `row` is the caller's own row, already locked: the reconcile route calls
    `lock_request_rows` for the whole request key before this (the same
    residual fix, item 1, `post_growth_queue_complete` applies before
    `record_receipt`), so there is no `queue_id` re-lock here and no
    ValueError('not_found') any more -- a missing row means the route's own
    lookup in `lock_request_rows`'s result already came back None, which it
    turns into a 404 before this is ever called.

    Returns `row` unchanged, so the caller can act on its
    `cancellation_requested_at`/subject the same way the complete route does.
    Raises ValueError('not_reconcilable') for any other row state."""
    if not external_post_id:
        raise ValueError('external_id_required')
    if row['status'] != 'review' or row['delivery_state'] not in RECOVERABLE_DELIVERY_STATES:
        raise ValueError('not_reconcilable')
    _mark_published(tx, row['id'], row, external_post_id, post_url)
    return row


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
    row = lock_queue_row(tx, queue_id)
    if not row:
        raise ValueError('not_found')
    if not row['lease_token'] or not hmac.compare_digest(row['lease_token'], lease_token):
        raise ValueError('lease_mismatch')
    # Fix wave item 1: a recoverable row (see RECOVERABLE_DELIVERY_STATES)
    # accepts a late `published` outcome from the very lease that produced
    # it, and is then treated exactly as a `processing` row would be. That is
    # the only way a post that really did go live can still be recorded --
    # and the only way withdrawal's removal task gets filed against it.
    # Every other non-processing case is refused as before.
    recoverable = (outcome == 'published' and row['status'] == 'review'
                   and row['delivery_state'] in RECOVERABLE_DELIVERY_STATES)
    if row['status'] != 'processing' and not recoverable:
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
        _mark_published(tx, queue_id, row, external_post_id, post_url)
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
        # Residual fix item 2: this outcome is only reached from a
        # `processing` row (the duplicate check above already answered
        # 'already' for a row that was parked in review with delivery_state
        # 'delivery_unknown' already). A member who withdrew WHILE it was
        # processing left only the cancellation stamp (withdraw_member step
        # 3 files no task for a still-processing row -- its lease holder was
        # still expected to report back). That lease holder just reported
        # back with nothing definite, so the row is now exactly what
        # withdrawal itself would have filed a task for had it arrived here
        # first.
        if row['cancellation_requested_at'] is not None:
            _file_investigate_task(tx, queue_id, row)
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
    'review' for a human to check (spec 5).

    Residual fix item 2: a row already carrying `cancellation_requested_at`
    (a member who withdrew while it was still `processing`, per the same
    reasoning as `record_receipt`'s `delivery_unknown` branch above) gets an
    `investigate` task the instant it lands here, rather than waiting for
    some later event that may never come."""
    rows = tx.execute(
        """UPDATE publishing_queue SET status = 'review', error = 'lease_expired',
                  lease_until = NULL, updated_at = NOW()
            WHERE status = 'processing' AND lease_until < NOW()
        RETURNING id, platform, request_key, subject_person_id, cancellation_requested_at""").fetchall()
    for row in rows:
        if row['cancellation_requested_at'] is not None:
            _file_investigate_task(tx, row['id'], row)
    return len(rows)


def settings(tx) -> dict:
    return {r['key']: r['value'] for r in tx.execute("SELECT key, value FROM spotlight_setting").fetchall()}


def set_setting(tx, key: str, value: str) -> None:
    if key in FREE_KEYS:
        if value is None:
            value = ''
        if not isinstance(value, str):
            raise ValueError('bad_setting')
    elif key not in SETTING_KEYS or value not in ('true', 'false'):
        raise ValueError('bad_setting')
    tx.execute("INSERT INTO spotlight_setting (key, value, updated_at) VALUES (%(k)s, %(v)s, NOW()) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
               dict(k=key, v=value))
