"""Community Spotlight publishing queue: the /admin/growth/* surface (spec 3.6, 3.8, 5).

Two callers share this surface. A human admin arrives with a bearer session;
the unattended scheduler arrives with the `X-Growth-Cron` shared secret and no
session at all.

DECORATOR NOTE (the judgement call the brief asks for). `service.api.decorators
.require_auth` with the default `auth='required'` -- which is what `aget`/`apost`
use -- returns `400 Missing or malformed authorization header` BEFORE the
handler body runs whenever there is no bearer token. A cron call has no bearer,
so registering the admin-or-cron routes with `aget`/`apost` would 400 every cron
request and `require_admin_or_cron` would never be reached. Those routes are
therefore registered with the UNAUTHENTICATED `get`/`post` decorators and
resolve the session here in `_session()`, which reuses the very same
`Q_GET_SESSION` query and `sha512` hashing that `require_auth` uses, so a bearer
behaves identically. Admin-only routes keep `aget`/`apost`: they always carry a
bearer, so the decorator's 400 is the correct answer for a missing one.

Transaction rule: nothing here opens an `api_tx` inside another one. The api
connection lock is not reentrant (Phase A deadlocked exactly this way), so
`_session()`, `require_admin()` and object-store uploads all happen strictly
outside the handler's own transaction. E4/E5 are the opposite case: since the
durable outbox landed they are QUEUED, not sent, so they take the handler's
own `tx` and commit with it. No SMTP runs anywhere in this module.
"""
from __future__ import annotations

import base64
import json
import re
import uuid as uuid_mod
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import duotypes as t
import psycopg
from flask import abort, jsonify, request

from database import api_tx
from duohash import sha512
from service.admin import record_audit, require_admin
from service.api.cron_auth import is_cron_request, require_admin_or_cron
from service.api.decorators import aget, apost, get, post, limiter, Q_GET_SESSION, _is_private_ip
from service.config import USER_IMAGES_BASE_URL
from service.spotlight.dispatch import dispatch_check
from service.spotlight.eligibility import eligibility, primary_photo_uuid, photo_url
from service.spotlight.occurrence import record_occurrence, pictured_people, is_first_confirmation
from service.spotlight.queue import (create_candidate, expire_member_approvals,
                                     lock_request_rows, set_status, settings, set_setting,
                                     reap_expired_leases, reconcile_published,
                                     record_receipt, OUTCOMES,
                                     OUTCOME_DELIVERY_STATE, PLATFORMS,
                                     SETTING_KEYS, FREE_KEYS)
from service.spotlight.assets import asset_key, attach_platform_image, complete_render_if_ready
from service.spotlight import cleanup
from service.spotlight.cleanup import (abandoned_job_rows, abandoned_jobs, enqueue_asset_delete,
                                       is_referenced, outstanding_jobs, overdue_removals)
from service.spotlight.revisions import (consent_complete, create_revision,
                                         current_revision, edit_caption)
from service.spotlight.roundup import roundup_snapshot
from service.spotlight.storage import InvalidImage
import service.spotlight.storage as st


def _growth_limit_exempt() -> bool:
    """Exempt the cron worker outright, on top of the existing private-IP
    exemption every shared limit already gets.

    This surface used to ride on unsubscribe's `unsub_limit` (20 per minute,
    scope `unsubscribe`), which is fine for one-off recipient clicks but wrong
    here: the admin worker's per-minute claim poll and its once-a-day tick
    call from the same Vercel egress IP, so the poll alone can exhaust a
    shared 20/minute bucket and the tick 503s. A valid cron header now clears
    the bucket entirely instead of spending from it."""
    return is_cron_request() or _is_private_ip()


# Dedicated bucket (scope `growth`) so this surface no longer competes with
# unsubscribe's recipient-click traffic; 120/minute is headroom for a human
# admin driving the queue UI, since cron itself is fully exempt above.
growth_limit = limiter.shared_limit(
    "120 per minute",
    scope="growth",
    exempt_when=_growth_limit_exempt,
)


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------

def _session() -> Optional[t.SessionInfo]:
    """Resolve the bearer token to a SessionInfo, or None.

    Deliberately mirrors `service.api.decorators.require_auth` (same header
    parse, same `Q_GET_SESSION`, same `sha512`) instead of inventing a second
    session format. Returns None rather than aborting so a cron caller with no
    bearer still reaches `require_admin_or_cron`.
    """
    auth_header = (request.headers.get('Authorization') or '').lower()
    try:
        bearer, session_token = auth_header.split()
        if bearer != 'bearer':
            return None
    except Exception:
        return None
    session_token_hash = sha512(session_token)
    with api_tx('READ COMMITTED') as tx:
        row = tx.execute(Q_GET_SESSION, dict(session_token_hash=session_token_hash)).fetchone()
    # `require_auth` defaults to expected_sign_in_status=True and
    # expected_onboarding_status=True and compares them against
    # `session_info.signed_in` and `session_info.person_id is not None`
    # (decorators.py lines 344-357). Both checks are repeated here, or a
    # pre-OTP duo_session row minted by POST /request-otp for an admin's
    # email would authenticate as that admin.
    if not row or not row['signed_in'] or row['person_id'] is None:
        return None
    return t.SessionInfo(
        email=row['email'],
        person_id=row['person_id'],
        person_uuid=row['person_uuid'],
        signed_in=row['signed_in'],
        session_token_hash=session_token_hash,
        pending_club_name=row['pending_club_name'],
    )


def _gate() -> Optional[t.SessionInfo]:
    """Resolve + authorise in one call. Both happen before any api_tx opens.

    The cron header is checked FIRST and short-circuits: a valid cron request
    carries no bearer, so resolving a session for it would open a transaction
    (and take the api connection lock) to look up a token that is not there.
    Every minute, on every growth call. `require_admin_or_cron` makes the same
    check, so the authorisation answer is unchanged either way.
    """
    if is_cron_request():
        return None
    s = _session()
    require_admin_or_cron(s)
    return s


def _body() -> dict:
    j = request.get_json(silent=True)
    return j if isinstance(j, dict) else {}


def _actor(s: Optional[t.SessionInfo]) -> str:
    return s.email if s is not None else 'cron'


def _is_int_or_none(v) -> bool:
    """`bool` is a subtype of `int` in Python; JSON's `true`/`false` must
    never pass a validation meant for a Graph error code or subcode."""
    return v is None or (isinstance(v, int) and not isinstance(v, bool))


def _audit(tx, s, action, **metadata):
    """Human mutations write an audit row inside the mutation's transaction.
    Cron has no actor to attribute, so it logs a line instead.

    `s` is None whenever `_gate()` saw a valid cron header: that check runs
    FIRST and short-circuits before any bearer is resolved, so a request that
    carries both a valid cron header and an admin's bearer is still treated as
    the cron actor here. Human audit rows come only from the admin-only
    routes registered with `aget`/`apost`, which always resolve a real
    session and never consult the cron header at all."""
    if s is not None:
        record_audit(tx, s, action, metadata=metadata)
    else:
        print(f'cron {action} {metadata}')


def _qid(value: str) -> uuid_mod.UUID:
    """publishing_queue.id is a native uuid column; a path segment is text."""
    try:
        return uuid_mod.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        abort(400)


def _plain(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, uuid_mod.UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row(r) -> dict:
    return {k: _plain(v) for k, v in r.items()}


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        abort(400)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _default_slot(now: Optional[datetime] = None) -> datetime:
    """The nearer of today 12:00 or 18:00 UTC still in the future, else
    tomorrow 12:00 UTC."""
    now = now or datetime.now(timezone.utc)
    for hour in (12, 18):
        slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot > now:
            return slot
    return (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Side effects (object store + the E4/E5 emails)
# ---------------------------------------------------------------------------

# Both helpers take the CALLER'S transaction and only queue: the message is
# an `email_outbox` row that commits with whatever decided to send it, and
# the `emailoutbox` cron does the actual SMTP (F07). They replace the old
# fire-and-forget daemon threads, which lost the email on any api restart and
# could not be retried.
#
# ENQUEUE-FAILURE POLICY, and it is deliberately different for the two:
#
#   E4 (card ready) PROPAGATES. The invite is the whole reason the candidate
#   exists: a member who is queued for a Spotlight card but has no record of
#   ever being asked is worse than no candidate at all, so a failure here
#   rolls the candidate back with it and the admin sees the error.
#
#   E5 (card live) is CAUGHT. By the time it runs the post is already live on
#   the platform and the receipt that proves it is in this transaction.
#   Losing that receipt over a failed email would orphan a real post that
#   nothing would then reconcile or remove. The failure is contained in a
#   savepoint (so a half-built message, e.g. its /s/ campaign link row, is
#   rolled back rather than committed as a stray) and reported back to the
#   caller, which audits it as `growth.email.enqueue_failed`.

_E5_SAVEPOINT = 'e5_enqueue'


def _enqueue_card_ready(tx, person_id: int, request_key: str) -> Optional[int]:
    from emails.spotlight_card_ready import enqueue_card_ready
    return enqueue_card_ready(tx, person_id, request_key)


def _enqueue_card_live(tx, person_id: int, request_key: str, external_post_id: str, platform: str,
                       post_url: Optional[str] = None) -> Optional[str]:
    """Returns None on success (including the ordinary "nothing to send"
    answers), or the error message when queuing E5 failed. The caller's
    transaction is left usable either way: a failure is rolled back to the
    savepoint, which also clears the aborted-transaction state a failed
    statement would otherwise leave behind."""
    from emails.spotlight_card_live import enqueue_card_live
    tx.execute(f'SAVEPOINT {_E5_SAVEPOINT}')
    try:
        enqueue_card_live(tx, person_id, request_key, external_post_id, platform, post_url)
    except Exception as e:      # noqa: BLE001 -- see the policy note above
        tx.execute(f'ROLLBACK TO SAVEPOINT {_E5_SAVEPOINT}')
        print(f'E5 enqueue failed for request {request_key}: {e}')
        return str(e)
    tx.execute(f'RELEASE SAVEPOINT {_E5_SAVEPOINT}')
    return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

# Fix round 1 ruling 2: a row cannot be re-rendered while a sibling of the
# same request_key is parked with its delivery unresolved --
# `assets.complete_render_if_ready`'s own residual fix refuses to complete
# the set at all in that state, so this surfaces the same fact rather than
# leaving an operator to infer it from a row stuck in awaiting_render or a
# review row with no render attached. `{q}` is the table alias the embedding
# query uses, the same shape `withdrawal._REISSUABLE` already establishes.
_RENDER_BLOCKED_PREDICATE = """(
    ({q}status = 'awaiting_render'
      OR ({q}status = 'review' AND NOT EXISTS (
            SELECT 1 FROM spotlight_revision rb
             WHERE rb.id = {q}current_revision_id AND rb.asset_hash IS NOT NULL)))
    AND EXISTS (SELECT 1 FROM publishing_queue rbs
                 WHERE rbs.request_key = {q}request_key AND rbs.id <> {q}id
                   AND rbs.delivery_state IN ('attempting', 'delivery_unknown'))
)"""

# Clicks and sign-ups are per REQUEST (both platform rows share the campaign
# link), counted off campaign_link.kind = 'post:<request_key>' and excluding
# the bot user-agent class so previews and crawlers don't inflate the number.
# q.member_approved_at and q.approved_photo_uuid (the pre-Wave-1 columns) are
# left untouched but no longer written by anything -- both are now sourced
# from the current revision and its consent row instead (Task 2).
_Q_ROWS = f"""
    SELECT q.id::text AS id,
           q.request_key,
           q.kind,
           q.platform,
           q.status,
           q.caption,
           q.image_url,
           q.image_key,
           q.post_url,
           q.delivery_state,
           q.scheduled_for,
           q.attempts,
           q.external_post_id,
           q.error,
           q.render_attempts,
           q.render_next_attempt_at,
           q.render_error,
           q.current_revision_id,
           r.revision AS revision_number,
           r.asset_hash AS revision_asset_hash,
           {_RENDER_BLOCKED_PREDICATE.format(q='q.')} AS render_blocked,
           (SELECT sc.approved_at FROM spotlight_revision_consent sc
              WHERE sc.revision_id = q.current_revision_id
                AND sc.role = 'subject' AND sc.person_id = q.subject_person_id) AS member_approved_at,
           q.subject_person_id,
           q.payload,
           split_part(p.name, ' ', 1) AS first_name,
           date_part('year', age(p.date_of_birth))::int AS age,
           COALESCE(p.country, p.location_short_friendly) AS country,
           COALESCE(
               r.photo_uuid::text,
               (SELECT ph.uuid::text FROM photo ph
                 WHERE ph.person_id = q.subject_person_id
                   AND ph.moderation_status = 'approved'
                 ORDER BY ph.position LIMIT 1)) AS photo_uuid,
           (SELECT count(*) FROM campaign_click c
              JOIN campaign_link l ON l.key = c.link_key
             WHERE l.kind = 'post:' || q.request_key
               AND c.ua_class <> 'bot')::int AS clicks,
           (SELECT count(DISTINCT c.signup_person_id) FROM campaign_click c
              JOIN campaign_link l ON l.key = c.link_key
             WHERE l.kind = 'post:' || q.request_key
               AND c.ua_class <> 'bot')::int AS signups
      FROM publishing_queue q
      LEFT JOIN person p ON p.id = q.subject_person_id
      LEFT JOIN spotlight_revision r ON r.id = q.current_revision_id
     WHERE (%(status)s::text IS NULL OR q.status = %(status)s::text)
       AND (%(due)s::bool IS NOT TRUE OR q.scheduled_for <= NOW())
       AND (%(needs_render)s::bool IS NOT TRUE
            OR (r.asset_hash IS NULL
                AND q.status NOT IN ('cancelled', 'published', 'processing', 'scheduled')))
       -- Wave 3d Task 4 (acceptance 8c): a card whose render was reported
       -- failed sits out its backoff (`post_growth_queue_render_failed`), so
       -- one that keeps failing cannot hold a place in the tick's 200 rows.
       AND (%(needs_render)s::bool IS NOT TRUE
            OR q.render_next_attempt_at IS NULL
            OR q.render_next_attempt_at <= NOW())
     -- The tick is served cards in the order they became eligible: a card
     -- never reported failed became eligible when it was created, a card in
     -- backoff when its backoff ends. Newest first, 205 newer failing cards
     -- hid an older one (8c). Plain oldest first (Task 4 fix round 1, C1) was
     -- no better for a tick that runs once a day: every backoff step under a
     -- day has passed by the next run, so the same ~100 oldest failing cards
     -- were handed out every day and a newer card that would render was
     -- never reached. A card that just failed now goes behind every card
     -- still waiting. The Growth tab (no filter) keeps newest first.
     ORDER BY CASE WHEN %(needs_render)s::bool
                   THEN COALESCE(q.render_next_attempt_at, q.created_at) END ASC NULLS LAST,
              q.created_at DESC, q.request_key, q.id
     LIMIT 200
"""

# Task 9 (F12): the welcome cohort is defined by sign-up time, not opt-in
# time -- opt-in can happen long after signing up (or be re-toggled), so
# ordering by it let a member who opted in years ago resurface as "new".
#
# Task 7 (Wave 3b): `q.status <> 'cancelled'` matches the guard
# post_growth_spotlight_welcome's own duplicate check already applies (fix
# wave item 5). A cancelled request is a request that did not happen, so a
# member whose welcome was cancelled is not a duplicate -- without this
# clause the write side would let them be offered a fresh welcome, but this
# read side (what the admin's /candidates list is actually built from) hid
# them forever, making that fix unreachable from the admin.
_Q_WELCOME_CANDIDATES = """
    SELECT p.id, p.name
      FROM person p
     WHERE p.sign_up_time > NOW() - interval '14 days'
       AND p.spotlight_opt_in IS TRUE
       AND NOT EXISTS (SELECT 1 FROM publishing_queue q
                        WHERE q.subject_person_id = p.id AND q.kind = 'welcome'
                          AND q.status <> 'cancelled')
     ORDER BY p.sign_up_time
     LIMIT 50
"""

# Task 9 (F12): a duplicate tick firing twice in the same week used to see no
# roundup "recent enough" (a 6-day lookback measured from each call's own
# NOW()) and create two. `_week_key` is the same business key
# `post_growth_spotlight_roundup` passes to `create_candidate`, so this is
# now an existence check on this week's key, not a rolling window.
_Q_ROUNDUP_THIS_WEEK = """
    SELECT EXISTS (SELECT 1 FROM publishing_queue WHERE request_key = %(rk)s) AS recent
"""

# Task 7 (the Wave 1 gap): a request is "invite pending" when its member-facing
# card exists but E4 was withheld -- created while approvals were off, per
# post_growth_spotlight_welcome/member_of_week's invite_sent guard. The
# predicate is shared by the count on GET /candidates and the rows the
# invite-pending route drains, so the two never disagree about what counts.
_Q_INVITES_PENDING_PREDICATE = """
    q.kind IN ('welcome', 'member_of_week')
      AND q.status = 'awaiting_member'
      AND q.cancellation_requested_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM email_outbox e
                       WHERE e.campaign = 'e4' AND e.campaign_id = 'e4-' || q.request_key)
"""

_Q_INVITES_PENDING_COUNT = f"""
    SELECT count(DISTINCT q.request_key) AS n FROM publishing_queue q WHERE {_Q_INVITES_PENDING_PREDICATE}
"""

# Task 7 (Wave 3b): the count alone cannot tell a one-day-old backlog from a
# two-week-old one. Computed in SQL (not by reading `created_at` back into
# Python and subtracting) so a null oldest (nothing pending) answers None
# without a separate branch. Nothing here cancels anything -- the backlog is
# only made visible, not swept.
_Q_INVITES_PENDING_OLDEST_DAYS = f"""
    SELECT floor(extract(epoch FROM NOW() - MIN(q.created_at)) / 86400)::int AS days
      FROM publishing_queue q WHERE {_Q_INVITES_PENDING_PREDICATE}
"""

_Q_INVITES_PENDING_ROWS = f"""
    SELECT q.request_key, q.subject_person_id, MIN(q.created_at) AS created_at
      FROM publishing_queue q
     WHERE {_Q_INVITES_PENDING_PREDICATE}
     GROUP BY q.request_key, q.subject_person_id
     ORDER BY MIN(q.created_at)
     LIMIT 50
"""


# Fix wave item 5: the only eligibility failures a withheld invite can never
# recover from. Everything else in `eligibility.REASONS` clears on its own --
# a report is dismissed, a verification lands, a birthday passes, the 30-day
# featured cooldown runs out, a photo is re-approved -- and cancelling on one
# of those threw the invite away for good, since nothing re-creates a
# cancelled request. Those count `skipped` and are re-checked next run.
#
# `not_activated` and `not_opted_in` are terminal because both mean consent
# is gone, and both already run `withdraw_member` on the way out; if a member
# activates or opts in again, the welcome route can create a fresh candidate
# (its duplicate guard ignores cancelled rows). `pending_deletion` is the
# member asking for the whole account to go.
TERMINAL_INVITE_REASONS = ('not_activated', 'not_opted_in', 'pending_deletion')


def _cancel_invite_pending_request(tx, request_key: str, error: str) -> None:
    """Fix round 1 ruling 1: a withheld invite that can never become sendable
    (the subject is no longer eligible, or there is nobody/no nonce to send
    it to) is cancelled outright rather than left to be re-considered and
    re-skipped by every future tick. `create_candidate` fans a welcome or
    member-of-week request out to one `publishing_queue` row per platform
    (same as a roundup), so every still-`awaiting_member` row of the key is
    cancelled here, not just one."""
    for row in tx.execute(
            "SELECT id::text AS id FROM publishing_queue WHERE request_key = %(rk)s AND status = 'awaiting_member'",
            dict(rk=request_key)).fetchall():
        set_status(tx, row['id'], 'cancelled', error=error)


def _week_key() -> str:
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    return f"roundup:{y}-W{w:02d}"


# The occurrence table (Wave 1 F05) is the source of truth for "when was this
# person last featured", not `person.spotlight_last_featured_at` (a
# denormalised display value only, per `service.spotlight.occurrence`). Both
# queries below aggregate it themselves rather than joining `record_occurrence`'s
# UPDATE target, so a person with zero occurrences still sorts correctly
# (never featured first).
_Q_LAST_FEATURED_GENDER = """
    SELECT g.name AS gender
      FROM (SELECT person_id, MAX(created_at) AS last_featured
              FROM spotlight_occurrence
             GROUP BY person_id) o
      JOIN person p ON p.id = o.person_id
      JOIN gender g ON g.id = p.gender_id
     ORDER BY o.last_featured DESC
     LIMIT 1
"""

# `g.name IS DISTINCT FROM <most recently featured gender>` sorts the opposite
# gender first without hardcoding a binary; with no one featured yet the
# parameter is NULL and the term is true for everyone, so it drops out.
_Q_SUGGEST = """
    SELECT p.id,
           split_part(p.name, ' ', 1) AS first_name,
           date_part('year', age(p.date_of_birth))::int AS age,
           COALESCE(p.country, p.location_short_friendly) AS country,
           o.last_featured AS spotlight_last_featured_at
      FROM person p
      LEFT JOIN gender g ON g.id = p.gender_id
      LEFT JOIN (SELECT person_id, MAX(created_at) AS last_featured
                   FROM spotlight_occurrence
                  GROUP BY person_id) o ON o.person_id = p.id
     WHERE p.spotlight_opt_in IS TRUE
     ORDER BY (g.name IS DISTINCT FROM %(recent)s::text) DESC,
              o.last_featured ASC NULLS FIRST,
              p.spotlight_opt_in_at ASC
     LIMIT 50
"""

# Task 6 (F09 part 3): `pending=1` is the worker's own drain -- only tasks
# that are actually due (`next_attempt_at <= NOW()`) and still actionable
# (never `needs_attention`, which a human has to clear). `attention=1` is the
# Growth tab's separate view of exactly the tasks `pending` withholds for that
# reason. Neither flag set returns every task, done or not, as before.
_Q_REMOVALS = """
    SELECT t.id,
           t.queue_id::text AS queue_id,
           t.platform,
           t.external_post_id,
           t.reason,
           t.done_at,
           t.created_at,
           -- Fix wave item 8: the working state of the task, not just its
           -- identity. Without these an operator could not tell a task that
           -- has been failing for two days from one filed a minute ago, and
           -- the `overdue` counter said how many were late without saying
           -- which. `_row` passes every column through, so selecting them is
           -- all the surface needs.
           t.attempts,
           t.last_error,
           t.next_attempt_at,
           t.deadline_at,
           t.evidence,
           q.request_key,
           q.kind,
           q.caption
      FROM spotlight_removal_task t
      LEFT JOIN publishing_queue q ON q.id = t.queue_id
     WHERE (%(pending)s::bool IS NOT TRUE
            OR (t.done_at IS NULL AND t.next_attempt_at <= NOW()
                AND t.reason IN ('delete_via_api', 'manual_instagram', 'investigate')))
       AND (%(attention)s::bool IS NOT TRUE
            OR (t.done_at IS NULL AND t.reason = 'needs_attention'))
       -- Wave 3d Task 4 fix round 1 (I1): `worker=1` is the admin worker's
       -- own drain, only the due tasks it can action. `manual_instagram` and
       -- `investigate` wait for a human and the worker skips them without
       -- moving `next_attempt_at`, so once more than 200 of those were
       -- overdue and sorted first they hid every `delete_via_api` task.
       -- `pending=1` keeps all three reasons for the operator panel.
       AND (%(worker)s::bool IS NOT TRUE
            OR (t.done_at IS NULL AND t.next_attempt_at <= NOW()
                AND t.reason = 'delete_via_api'))
     -- Wave 3d Task 4 (acceptance 8c): due tasks (`pending=1`, `worker=1`)
     -- are served closest to their removal deadline first, ties oldest task
     -- first; newest first handed out the newest 200 of 8198 due tasks and
     -- left the oldest to go overdue. The attention view and the unfiltered
     -- list keep newest first.
     ORDER BY CASE WHEN %(pending)s::bool OR %(worker)s::bool THEN t.deadline_at END ASC NULLS LAST,
              CASE WHEN %(pending)s::bool OR %(worker)s::bool THEN t.id END ASC,
              t.created_at DESC
     LIMIT 200
"""


def _queue_row(r, consent_ok: Optional[bool] = None) -> dict:
    subject = None
    if r['subject_person_id'] is not None:
        subject = dict(
            person_id=r['subject_person_id'],
            first_name=r['first_name'],
            age=r['age'],
            country=r['country'],
            photo_url=photo_url(r['photo_uuid']) if r['photo_uuid'] else None,
        )
    row = dict(
        id=r['id'],
        request_key=r['request_key'],
        kind=r['kind'],
        platform=r['platform'],
        status=r['status'],
        caption=r['caption'],
        image_url=r['image_url'],
        # `preview_url` defaults to the public `image_url`; the caller
        # (get_growth_queue) overwrites it with a presigned URL once the
        # transaction that produced these rows has closed, since storage
        # calls must never run inside an open api_tx.
        preview_url=r['image_url'],
        post_url=r['post_url'],
        delivery_state=r['delivery_state'],
        scheduled_for=_plain(r['scheduled_for']),
        attempts=r['attempts'],
        external_post_id=r['external_post_id'],
        error=r['error'],
        member_approved_at=_plain(r['member_approved_at']),
        subject=subject,
        clicks=r['clicks'],
        signups=r['signups'],
        revision=r['revision_number'],
        consent_complete=consent_ok,
        render_blocked=bool(r['render_blocked']),
        # Wave 3d Task 4: additive. How many renders have failed since the
        # last successful attach, when the tick may try again, and the last
        # sanitised reason (never `error`, which publishing owns).
        render_attempts=r['render_attempts'],
        render_next_attempt_at=_plain(r['render_next_attempt_at']),
        render_error=r['render_error'],
    )
    # Only kind 'roundup' rows carry a payload snapshot (stamped at creation
    # by post_growth_spotlight_roundup); every other kind leaves these keys
    # out entirely rather than shipping them as null.
    if r['kind'] == 'roundup' and r['payload']:
        row['tiles'] = r['payload'].get('tiles', [])
        row['count'] = r['payload'].get('count')
        row['countries'] = r['payload'].get('countries')
    return row


# ---------------------------------------------------------------------------
# Admin-or-cron endpoints (unauthenticated decorators; see DECORATOR NOTE)
# ---------------------------------------------------------------------------

@get('/admin/growth/queue', limiter=growth_limit)
def get_growth_queue():
    _gate()
    status = request.args.get('status') or None
    due = request.args.get('due') in ('1', 'true', 'yes')
    # needs_render replaces status=awaiting_render as the render tick's real
    # selector (Task 2): a subject row stays `awaiting_member` with approvals
    # disabled, but still needs rendering, so it must be picked up here too.
    # The old status filter keeps working unchanged for compatibility.
    needs_render = request.args.get('needs_render') in ('1', 'true', 'yes')
    with api_tx('read committed') as tx:
        rows = tx.execute(_Q_ROWS, dict(status=status, due=due, needs_render=needs_render)).fetchall()
        # Memoised per revision_id: both platform rows of a request share one
        # current revision, so this never computes the same answer twice.
        cache: dict = {}
        out = []
        for r in rows:
            rid = r['current_revision_id']
            if rid is not None and rid not in cache:
                cache[rid] = consent_complete(tx, rid)
            out.append(_queue_row(r, cache.get(rid)))
    # Presigning talks to storage (an outbound call), which must never run
    # inside an open api_tx (the api connection lock is not reentrant), so
    # this runs after the block above rather than inside _queue_row.
    from service.spotlight import storage
    for r, row in zip(rows, out):
        key = r['image_key']
        if key:
            row['preview_url'] = storage.presign(key) or row['image_url']
    return jsonify(out)


@post('/admin/growth/queue/claim', limiter=growth_limit)
def post_growth_queue_claim():
    s = _gate()
    body = _body()
    try:
        requested = int(body.get('max') or 2)
    except (TypeError, ValueError):
        abort(400)
    requested = max(1, min(requested, 50))
    with api_tx() as tx:
        # Reaping runs on every call regardless of the two controls below: an
        # interrupted publish can leave a lease to expire whether or not
        # anything may currently be claimed, and reaping only ever moves a
        # row to 'review' -- it never publishes anything itself.
        reaped = reap_expired_leases(tx)
        cfg = settings(tx)
        # Task 9 (F12): `external_access_enabled` is the emergency stop for
        # every outbound platform call; `publication_enabled` is the
        # ordinary pause. Both answer through this one object now -- the
        # `shape` body key from the old bare-array default is gone.
        halted = cfg.get('external_access_enabled') != 'true'
        paused = cfg.get('publication_enabled') != 'true'
        if halted or paused:
            rows = []
        else:
            rows = tx.execute("SELECT * FROM claim_spotlight_posts(%(m)s)", dict(m=requested)).fetchall()
        _audit(tx, s, 'growth.queue.claim', claimed=len(rows), reaped=reaped, paused=paused, halted=halted)
    claimed = [_row(r) for r in rows]
    return jsonify({'claimed': claimed, 'reaped': reaped, 'paused': paused, 'halted': halted})


@post('/admin/growth/queue/<qid>/complete', limiter=growth_limit)
def post_growth_queue_complete(qid: str):
    """The worker's receipt for one lease's dispatch attempt (Wave 1 F04).
    `record_receipt` does the real work and binds the receipt to the exact
    lease `claim_spotlight_posts` handed out; this route only maps its
    outcomes onto HTTP and decides whether the live-card email may fire.

    A withdrawn member must never receive "your card is live": the row's
    `cancellation_requested_at` is read in the SAME SELECT used for the
    email decision, before `record_receipt` runs, so a late `published`
    receipt against a withdrawn member's row is recorded (and its removal
    task filed, inside `record_receipt` itself) without ever sending it.

    Residual fix (item 1): the lock taken here covers every row of the
    request key, not just this one -- a sibling platform row completing at
    the same moment takes the same wide lock before touching anything, so the
    two can never each hold one row and block on the other's (see
    `lock_request_rows`'s docstring in service/spotlight/queue.py).
    `record_receipt` re-locks its own row in the same transaction, which
    Postgres grants outright.
    """
    s = _gate()
    body = _body()
    status = body.get('status')
    lease_token = body.get('lease_token')
    external_post_id = body.get('external_post_id')
    post_url = body.get('post_url')
    error = body.get('error')
    queue_id = _qid(qid)
    occurrences = None
    first_confirmation = None
    with api_tx() as tx:
        # The request-wide lock is taken HERE, before anything is read off
        # any row of the key, so the `cancellation_requested_at` this route
        # gates E5 on cannot change between that read and `record_receipt`'s
        # own write.
        rows = lock_request_rows(tx, queue_id)
        row = next((r for r in rows if r['id'] == queue_id), None)
        if not row:
            abort(404)
        try:
            result = record_receipt(tx, queue_id, lease_token, status,
                                    external_post_id=external_post_id, post_url=post_url, error=error)
        except ValueError as e:
            reason = str(e)
            if reason == 'not_found':
                abort(404)
            code = 409 if reason in ('lease_mismatch', 'not_processing') else 400
            return dict(error=reason), code
        if result == 'recorded' and status == 'published':
            # The cooldown unit is the feature OCCURRENCE (one per request_key
            # and person), not the platform row (Wave 1 F05): stamping it here,
            # once per confirmed publish, means a card's second platform to
            # confirm never re-blocks itself on its own sibling's stamp.
            people = pictured_people(tx, row)
            occurrences = record_occurrence(tx, row['kind'], row['request_key'], people)
            first_confirmation = is_first_confirmation(tx, row['request_key'])
            # E5 ("your card is live") fires once per occurrence, on whichever
            # platform confirms first -- never once per platform row, and
            # never for a roundup (no subject_person_id).
            if (first_confirmation and row['subject_person_id'] is not None
                    and row['cancellation_requested_at'] is None):
                live = (row['subject_person_id'], row['request_key'],
                        external_post_id or '', row['platform'], post_url)
                # Inside the transaction on purpose (F07): the email is a
                # queued row that commits with the receipt that earned it. A
                # failure to queue it never takes the receipt down with it
                # (see the enqueue-failure policy above); it is audited so the
                # member can be mailed by hand.
                enqueue_error = _enqueue_card_live(tx, *live)
                if enqueue_error is not None:
                    _audit(tx, s, 'growth.email.enqueue_failed', campaign='e5',
                           request_key=row['request_key'], error=enqueue_error)
        # A duplicate ('already') receipt changed nothing -- record_receipt's
        # own no-writes guarantee -- so it earns no audit row either; only a
        # receipt that actually moved the row is logged.
        if result == 'recorded':
            audit_metadata = dict(queue_id=str(queue_id), status=status,
                                   delivery_state=OUTCOME_DELIVERY_STATE.get(status))
            if occurrences is not None:
                audit_metadata['occurrences'] = occurrences
                audit_metadata['first_confirmation'] = first_confirmation
            _audit(tx, s, 'growth.queue.complete', **audit_metadata)
    return dict(ok=True, status=status, already=(result == 'already'))


@get('/admin/growth/queue/<qid>/eligible', limiter=growth_limit)
def get_growth_queue_eligible(qid: str):
    """The publish worker's final check before it dispatches a row, and the
    only thing that check is allowed to do (F02 remediation): delegate to
    `dispatch_check`, which fails closed on every condition rather than only
    the ones a payload snapshot happens to carry. `lease_token` binds the
    answer to the specific lease `claim_spotlight_posts` handed the caller;
    without one the row is refused outright rather than treated as eligible."""
    _gate()
    queue_id = _qid(qid)
    lease_token = request.args.get('lease_token')
    with api_tx('read committed') as tx:
        ok, reason = dispatch_check(tx, queue_id, lease_token)
    return dict(ok=ok, reason=reason)


@post('/admin/growth/queue/<request_key>/image', limiter=growth_limit)
def post_growth_queue_image(request_key: str):
    s = _gate()
    body = _body()
    platform = body.get('platform')
    if platform not in PLATFORMS:
        abort(400)
    # Validate the payload BEFORE anything reaches the object store: a
    # mislabelled, truncated or wrong-size upload must not leave a
    # half-written key behind. sha256 is the accepted bytes' content hash,
    # used below both as the immutable key's identity and as the row's own
    # dedup stamp.
    # Wave 3d Task 6: the admin now sends one JPEG card (Instagram publishes
    # JPEG only) as `image_base64` with its `content_type`. The old
    # `png_base64` field is still taken, as a PNG, so the admin deployed
    # before this change keeps working until it is redeployed.
    if body.get('image_base64') is not None:
        encoded = body.get('image_base64')
        content_type = body.get('content_type')
        if not isinstance(content_type, str) or content_type not in st.CARD_IMAGE_TYPES:
            abort(400)
    else:
        encoded = body.get('png_base64') or ''
        content_type = 'image/png'
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception:
        abort(400)
    try:
        sha256 = st.validate_card_image(data, content_type)
    except InvalidImage as e:
        return dict(error='invalid_image', reason=str(e)), 400

    with api_tx('read committed') as tx:
        known = tx.execute(
            """SELECT status, current_revision_id FROM publishing_queue
                WHERE request_key = %(rk)s AND platform = %(pl)s""",
            dict(rk=request_key, pl=platform)).fetchone()
    if not known:
        abort(404)
    # Fix round 1 (Task 2 review): both checks below run BEFORE the upload,
    # so a rejected call never reaches the object store and never writes a
    # row's image columns.
    if known['current_revision_id'] is None:
        return dict(error='no_revision'), 409
    with api_tx('read committed') as tx:
        rev = tx.execute("SELECT asset_hash FROM spotlight_revision WHERE id = %(id)s",
                         dict(id=known['current_revision_id'])).fetchone()
    if rev and rev['asset_hash'] is not None:
        return dict(error='already_rendered'), 409
    # A row that is already scheduled, leased, published or cancelled must
    # not have its artwork swapped underneath it.
    if known['status'] not in ('awaiting_member', 'awaiting_render', 'review'):
        return dict(error='bad_status'), 409

    revision_id = known['current_revision_id']
    key = asset_key(request_key, revision_id, sha256, platform, content_type)
    url = f'{USER_IMAGES_BASE_URL}/{key}'

    # Upload outside any transaction: a network round trip must not hold the
    # api connection lock. Private by default (Wave 2 F09) -- the object is
    # not publicly reachable until the card is approved and scheduled.
    # Fix round 1 (Task 2 review): an unconfigured object store now raises
    # RuntimeError('storage_unconfigured') up front rather than silently
    # dropping the bytes; mapped here the same way an approve-time storage
    # failure already is, below.
    try:
        st.put_card_image(key, data, content_type)
    except RuntimeError as e:
        if str(e) != 'storage_unconfigured':
            raise
        return dict(error='storage_unavailable'), 503

    # The revision id and status were read before the upload, and the upload
    # is a network round trip: a caption edit (new revision), an approve or a
    # cancel can land in between. attach_platform_image's own WHERE repeats
    # both checks as a compare-and-set, so the row is only stamped if it is
    # still pinned to the exact revision this upload was rendered against and
    # still in a status that may have its artwork replaced. 'superseded'
    # means the just-uploaded object is now an orphan -- nothing points at it
    # and nothing ever will -- so it is queued for deletion rather than left
    # behind.
    # Wave 3d Task 3 (Runtime 8a): two uploads for the same row can pass every
    # check above together; the one that reaches the row lock second fails
    # (SerializationFailure under REPEATABLE READ, the evidence's case) once
    # the first commits. The winner attached the artwork, so the loser
    # answers 409 image_race rather than 500, and the admin tick reads that
    # as rendered by another run. Caught outside the `with` block so the
    # loser's transaction rolls back first; not retried.
    try:
        with api_tx() as tx:
            outcome, previous_key = attach_platform_image(tx, request_key, platform, revision_id,
                                                          key, url, sha256)
            if previous_key and previous_key != key and not is_referenced(tx, previous_key):
                # Fix wave item 6: this upload displaced an earlier one. Keys are
                # content-hashed, so different bytes for the same revision land on
                # a different key and nothing names the old object any more -- the
                # ordinary case while an operator iterates on artwork, and the one
                # the superseded branch below never covered. Guarded by the same
                # reference check: a sibling platform row or a revision may still
                # name it (identical bytes share a key), in which case there is no
                # orphan to clean up.
                enqueue_asset_delete(tx, previous_key)
            if outcome == 'superseded' and not is_referenced(tx, key):
                # Nothing points at the just-uploaded object and nothing ever
                # will, so it is queued for deletion (Wave 2 Task 5) rather than
                # deleted inline: enqueueing is a database write and commits with
                # this transaction, where a storage call would have held the api
                # connection lock across a network round trip.
                #
                # Fix round 1, ruling 2: guarded by the same reference check the
                # cleanup batch re-runs. The key is content-hashed, so a re-upload
                # of identical bytes lands on the identical key -- if a live row
                # or revision still names it, the object is in use and there is no
                # orphan to clean up.
                enqueue_asset_delete(tx, key)
            if outcome == 'attached':
                if complete_render_if_ready(tx, request_key, revision_id):
                    # A roundup (no subject) row is ready to schedule as soon as
                    # it is rendered. A subject row stays `awaiting_member` even
                    # once rendered -- approve_card is what moves it on to
                    # review, and only once the member actually consents.
                    tx.execute(
                        """UPDATE publishing_queue SET status = 'review', updated_at = NOW()
                            WHERE request_key = %(rk)s AND status = 'awaiting_render'""",
                        dict(rk=request_key))
                _audit(tx, s, 'growth.queue.image', request_key=request_key, platform=platform)
    except (psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure):
        # Same orphan rule as 'superseded' below: identical bytes land on the
        # winner's key, which a row now names, so nothing is queued; different
        # bytes left an object nothing will ever point at.
        with api_tx() as tx:
            if not is_referenced(tx, key):
                enqueue_asset_delete(tx, key)
        return dict(error='image_race'), 409
    if outcome == 'superseded':
        return dict(error='superseded'), 409
    return dict(image_url=url)


# Wave 3d Task 4 (acceptance 8c). A reason is whatever the renderer threw, and
# a photo fetch can throw with the photo's signed URL in its message, so every
# URL is removed before the reason is stored and the rest is capped. Fix round
# 1 (M1): also a URL written without its scheme (`//host/...`), and any word
# carrying a signature or token on its own (`X-Amz-...`, `Signature=`,
# `Credential=`, `?token=`, `&sig=`). The admin tick applies the same rule.
# Fix wave B (T4 m3): also `access_token=...` quoted inside a sentence and a
# bare `token=...` or `sig=...` with no `?` or `&` in front of it.
_URL_IN_REASON = re.compile(
    r'(?:https?:)?//\S+|\S*(?:X-Amz-|(?:access_)?token=|sig=|Signature=|Credential=)\S*',
    re.IGNORECASE)
RENDER_REASON_MAX = 200
# Fix wave B (T4 m4): what a reason that was nothing but a URL (or a token)
# is stored as, so the failure never reads back as blank.
RENDER_REASON_REDACTED = 'render_failed_url_redacted'

# 15 minutes, doubled for every failure already recorded, capped at 24 hours:
# 15m, 30m, 1h, 2h, 4h, 8h, 16h, then 24h. The exponent is clamped at 7
# (15 minutes * 2^7 is already past the cap), which changes no answer but
# keeps a card that has failed for months from overflowing the interval.
# `render_attempts` on the right-hand side is the value before this update.
# `updated_at` is left alone on purpose: this is render bookkeeping, and the
# retention sweep dates a published row's artwork by `updated_at`.
_Q_RENDER_FAILED = """
    UPDATE publishing_queue
       SET render_attempts = render_attempts + 1,
           render_next_attempt_at = NOW() + LEAST(interval '15 minutes' * 2 ^ LEAST(render_attempts, 7),
                                                  interval '24 hours'),
           render_error = %(reason)s
     WHERE request_key = %(rk)s
 RETURNING render_attempts, render_next_attempt_at
"""


def _render_reason(value: str) -> Optional[str]:
    """No URL (so no signed URL or token), whitespace collapsed where one was
    cut out, at most RENDER_REASON_MAX characters. Stripped before the cap, so
    a URL can never survive by being cut short. A reason with text that
    sanitises to nothing is stored as RENDER_REASON_REDACTED rather than NULL
    (fix wave B, T4 m4); a blank reason stays NULL."""
    cleaned = ' '.join(_URL_IN_REASON.sub(' ', value).split())[:RENDER_REASON_MAX].strip()
    if not cleaned and value.strip():
        return RENDER_REASON_REDACTED
    return cleaned or None


def _lock_render_rows(tx, request_key: str) -> list:
    """Every row of the request, locked in one fixed order, so two reports for
    the same card (a duplicate cron run) queue behind each other rather than
    each holding one platform row and waiting on the other's."""
    return tx.execute(
        "SELECT id FROM publishing_queue WHERE request_key = %(rk)s ORDER BY id FOR UPDATE",
        dict(rk=request_key)).fetchall()


@post('/admin/growth/queue/<request_key>/render-failed', limiter=growth_limit)
def post_growth_queue_render_failed(request_key: str):
    """The render tick reports a card it could not render or upload (a lost
    render race is not reported). Every row of the request backs off, so a
    card that keeps failing stops taking a place in the tick's listing (served
    in the order cards became eligible) until its time passes; a successful
    attach resets its row (`attach_platform_image`).

    READ COMMITTED, not the default snapshot level: two reports racing on the
    same card must both count, and under a snapshot the second writer would
    fail on the row the first one updated instead of re-reading it."""
    s = _gate()
    reason = _body().get('reason')
    if not isinstance(reason, str):
        return dict(error='bad_request'), 400
    stored = _render_reason(reason)
    with api_tx('read committed') as tx:
        # Fix round 1 (M3): during a duplicate cron run this can, rarely,
        # deadlock with the other run's image attach on the same card (the
        # attach holds its own platform row and, once the set completes,
        # moves every row of the key to review; this locks both in id order).
        # Postgres aborts one side: either the report 500s (the tick swallows
        # it) or the attach does (a render failure the tick reports). Both
        # heal on the next run, which lists the card again.
        if not _lock_render_rows(tx, request_key):
            abort(404)
        rows = tx.execute(_Q_RENDER_FAILED, dict(rk=request_key, reason=stored)).fetchall()
        attempts = max(r['render_attempts'] for r in rows)
        next_attempt_at = max(r['render_next_attempt_at'] for r in rows)
        _audit(tx, s, 'growth.queue.render_failed', request_key=request_key, render_attempts=attempts)
    return dict(request_key=request_key, render_attempts=attempts,
                render_next_attempt_at=_plain(next_attempt_at))


@get('/admin/growth/candidates', limiter=growth_limit)
def get_growth_candidates():
    _gate()
    with api_tx('read committed') as tx:
        # Task 9 (F12): invites off means no new welcome or roundup should
        # even be suggested, not just refused on creation.
        if settings(tx).get('invites_enabled') != 'true':
            return dict(welcomes=[], roundup_due=False, invites_enabled=False, invites_pending=0,
                        invites_pending_oldest_days=None)
        rows = tx.execute(_Q_WELCOME_CANDIDATES).fetchall()
        welcomes = []
        for r in rows:
            ok, _reason = eligibility(tx, r['id'])
            if ok:
                welcomes.append(dict(person_id=r['id'], first_name=(r['name'] or '').split(' ')[0]))
        recent_roundup = tx.execute(_Q_ROUNDUP_THIS_WEEK, dict(rk=_week_key())).fetchone()['recent']
        # Task 7: reported even while approvals are off -- it is exactly what
        # invite-pending will drain once they open, so the tick (and a human
        # on the Growth tab) can see the backlog building before that.
        invites_pending = int(tx.execute(_Q_INVITES_PENDING_COUNT).fetchone()['n'])
        # Task 7 (Wave 3b): the backlog's age, not just its size.
        invites_pending_oldest_days = tx.execute(_Q_INVITES_PENDING_OLDEST_DAYS).fetchone()['days']
    # Monday is weekday() == 0.
    roundup_due = datetime.now(timezone.utc).weekday() == 0 and not recent_roundup
    return dict(welcomes=welcomes, roundup_due=roundup_due, invites_enabled=True,
                invites_pending=invites_pending, invites_pending_oldest_days=invites_pending_oldest_days)


@post('/admin/growth/spotlight/welcome', limiter=growth_limit)
def post_growth_spotlight_welcome():
    s = _gate()
    person_id = _body().get('person_id')
    if not person_id:
        abort(400)
    # Wave 3d Task 3 (Runtime 8a): the `already` guard below is a read, and
    # every racing worker passes it together. Migration 0050's partial unique
    # index is the same guard in database form, so a racing loser fails on
    # insert instead of creating a second card and a second E4. The except
    # sits OUTSIDE the `with` block so the loser's transaction has already
    # rolled back (its candidate and its invite with it) before it answers,
    # the same shape `post_spotlight_card` uses. Either error can surface
    # depending on timing under REPEATABLE READ. Not retried: the winner's
    # welcome is the answer.
    try:
        with api_tx() as tx:
            cfg = settings(tx)
            # Task 9 (F12): invites_enabled gates candidate creation and E4.
            if cfg.get('invites_enabled') != 'true':
                return dict(error='invites_paused'), 409
            # Fix wave item 5: a cancelled row is a request that did not happen,
            # so it is not a duplicate. The guard matched on kind alone, which
            # meant a member whose welcome was cancelled (by invite-pending's
            # terminal path, or a withdrawal they have since reversed) could never
            # be offered one again -- this route answered 409 forever and nothing
            # re-creates a cancelled request. Every other status still blocks: the
            # guard exists to stop two live welcome cards for the same member.
            already = tx.execute(
                """SELECT 1 FROM publishing_queue
                    WHERE subject_person_id = %(p)s AND kind = 'welcome'
                      AND status <> 'cancelled' LIMIT 1""",
                dict(p=person_id)).fetchone()
            if already:
                abort(409)
            info = tx.execute(
                """SELECT split_part(name, ' ', 1) AS first_name,
                          COALESCE(country, location_short_friendly) AS country
                     FROM person WHERE id = %(p)s""",
                dict(p=person_id)).fetchone()
            if not info:
                abort(404)
            caption = _welcome_caption(info['first_name'], info['country'])
            try:
                rk = create_candidate(tx, kind='welcome', subject_person_id=person_id,
                                      caption=caption, created_by=_actor(s))
            except ValueError as e:
                abort(400, str(e))
            # Added from the Task 2 review: a member must never receive an
            # invite they cannot act on. Approvals off means there is no way to
            # approve the card yet, so the candidate is created (ready the
            # moment approvals resume) but E4 is withheld.
            invite_sent = cfg.get('approvals_enabled') == 'true'
            if invite_sent:
                # Inside the transaction on purpose (F07): the candidate and the
                # invite that announces it either both land or neither does. A
                # failure here is NOT caught (unlike E5 on the receipt routes):
                # no candidate without its invite record.
                _enqueue_card_ready(tx, person_id, rk)
            _audit(tx, s, 'growth.queue.welcome', person_id=person_id, request_key=rk, invite_sent=invite_sent)
    except (psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure):
        abort(409)
    return dict(request_key=rk)


@post('/admin/growth/spotlight/roundup', limiter=growth_limit)
def post_growth_spotlight_roundup():
    s = _gate()
    week_key = _week_key()
    # Wave 3d Task 3 (Runtime 8a): the `already` read below converges a
    # duplicate that arrives after the first roundup committed, but racing
    # calls all pass it together and the losers fail on the
    # (request_key, platform) unique key. The occurrence exists, so a loser
    # answers the same "already" a later duplicate would, not a 500. Caught
    # outside the `with` block so its transaction rolls back first.
    try:
        with api_tx() as tx:
            # Task 9 (F12): invites_enabled gates candidate creation and E4.
            if settings(tx).get('invites_enabled') != 'true':
                return dict(error='invites_paused'), 409
            # The weekly business key converges: a duplicate tick in the same
            # week gets told "already" rather than creating a second roundup.
            if tx.execute("SELECT 1 FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1",
                          dict(rk=week_key)).fetchone():
                return dict(request_key=week_key, already=True)
            snapshot = roundup_snapshot(tx)
            caption = _roundup_caption(snapshot['count'], snapshot['countries'])
            rk = create_candidate(tx, kind='roundup', subject_person_id=None,
                                  caption=caption, created_by=_actor(s), request_key=week_key)
            tx.execute(
                "UPDATE publishing_queue SET payload = %(p)s::jsonb WHERE request_key = %(rk)s",
                dict(p=json.dumps(snapshot), rk=rk))
            # Owner decision (Task 8): count-only unless roundup_tiles_enabled is
            # on. `roundup_snapshot` already returns `tiles=[]` in that case, so
            # revision 1 from create_candidate (empty participants) stands --
            # nothing else to do. With tiles, a fresh revision records every
            # tiled member as a participant, so the fail-closed dispatch check
            # (consent_complete) refuses the card until each one consents.
            count_only = not snapshot['tiles']
            if not count_only:
                # Fix wave I1: read the caption off the CURRENT REVISION, not off
                # an arbitrary queue row. Each row's caption now carries its own
                # `?p=` platform stamp, so `LIMIT 1` over the rows would have
                # frozen one platform's stamped link into the shared, immutable
                # revision every row points at. The revision's own caption is the
                # platform-neutral form `create_candidate` wrote.
                caption_row = current_revision(tx, rk)
                create_revision(
                    tx, rk, caption=caption_row['caption'], photo_uuid=None,
                    participants=[dict(person_id=tile['person_id'], first_name=tile['first_name'],
                                       photo_url=tile['photo_url'], photo_uuid=tile['photo_uuid'])
                                  for tile in snapshot['tiles']],
                    # Fix round 1 (ruling 2): the same PLATFORMS constant
                    # create_candidate inserted rows with, not an unordered
                    # SELECT over those rows.
                    channels=list(PLATFORMS), created_by=_actor(s))
            _audit(tx, s, 'growth.queue.roundup', request_key=rk,
                   tiles=len(snapshot['tiles']), count=snapshot['count'], count_only=count_only)
    except (psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure):
        return dict(request_key=week_key, already=True)
    return dict(request_key=rk)


@post('/admin/growth/spotlight/invite-pending', limiter=growth_limit)
def post_growth_spotlight_invite_pending():
    """Task 7 (the Wave 1 gap): sends the E4 invites that were withheld while
    approvals were paused. `post_growth_spotlight_welcome` and
    `post_growth_spotlight_member_of_week` both create the candidate but skip
    E4 when approvals are off, so nothing ever comes back and asks the member
    to act -- this is what drains that backlog once approvals resume, and the
    tick calls it every run `candidates.invites_pending` is above zero.

    Fix round 1 ruling 1: a backlog entry that can never become sendable must
    reach a TERMINAL state rather than sit in `invites_pending` forever, so
    its request is cancelled outright -- same for the rarer case where
    `enqueue_card_ready` itself cannot address the invite (no activated
    person, no card nonce) and left no outbox row behind. A `None` WITH an
    outbox row already present is the ordinary idempotent case (a retried
    call, or a race with another producer) and is simply skipped: the
    predicate that built `rows` will drop it on its own the moment that row
    exists.

    Fix wave item 5 narrowed "can never become sendable" to
    TERMINAL_INVITE_REASONS. Most eligibility failures are temporary, and
    cancelling on one threw the invite away for good, since nothing
    re-creates a cancelled request: a member whose report was dismissed the
    next day simply never got their card. Those now count `skipped` and are
    re-checked on the following run."""
    s = _gate()
    with api_tx('read committed') as tx:
        cfg = settings(tx)
        if cfg.get('approvals_enabled') != 'true':
            return dict(error='approvals_paused'), 409
        if cfg.get('invites_enabled') != 'true':
            return dict(error='invites_paused'), 409
        rows = tx.execute(_Q_INVITES_PENDING_ROWS).fetchall()
    queued = skipped = cancelled = 0
    for r in rows:
        rk = r['request_key']
        # One transaction per request (the brief's rule): the eligibility
        # re-check and the enqueue (or the cancellation) either both land or
        # neither does, and one ineligible or already-sent request never
        # blocks the rest of the batch.
        with api_tx() as tx:
            ok, reason = eligibility(tx, r['subject_person_id'], exclude_request_key=rk)
            if not ok:
                if reason in TERMINAL_INVITE_REASONS:
                    _cancel_invite_pending_request(tx, rk, f'invite_skipped:{reason}')
                    cancelled += 1
                else:
                    # Recoverable (fix wave item 5): the request stays exactly
                    # as it is and the next run reconsiders it. It keeps
                    # counting towards `invites_pending`, which is the honest
                    # reading -- the invite really is still owed.
                    skipped += 1
                continue
            oid = _enqueue_card_ready(tx, r['subject_person_id'], rk)
            if oid is not None:
                queued += 1
                continue
            has_outbox = tx.execute(
                "SELECT 1 FROM email_outbox WHERE campaign = 'e4' AND campaign_id = %(cid)s LIMIT 1",
                dict(cid=f'e4-{rk}')).fetchone()
            if has_outbox:
                skipped += 1
            else:
                _cancel_invite_pending_request(tx, rk, 'invite_skipped:no_person')
                cancelled += 1
    with api_tx() as tx:
        _audit(tx, s, 'growth.invite_pending', queued=queued, skipped=skipped, cancelled=cancelled)
    return dict(queued=queued, skipped=skipped, cancelled=cancelled)


@post('/admin/growth/queue/expire-approvals', limiter=growth_limit)
def post_growth_queue_expire_approvals():
    s = _gate()
    with api_tx() as tx:
        # Added from the Task 2 review: the 7-day clock only runs while a
        # member can act on it. With approvals off nobody can approve a
        # card, so the sweep must not cancel anything for having sat idle.
        if settings(tx).get('approvals_enabled') != 'true':
            _audit(tx, s, 'growth.queue.expire_approvals', cancelled=0, approvals_paused=True)
            return dict(cancelled=0, approvals_paused=True)
        n = expire_member_approvals(tx)
        _audit(tx, s, 'growth.queue.expire_approvals', cancelled=n)
    return dict(cancelled=n)


@get('/admin/growth/settings', limiter=growth_limit)
def get_growth_settings():
    _gate()
    with api_tx('read committed') as tx:
        cfg = settings(tx)
    # Task 9 (F12): only the five live controls plus the two free (non-flag)
    # keys are ever answered here -- a stray row left behind by an old
    # migration or a bad write must never resurface on this surface.
    return {k: v for k, v in cfg.items() if k in SETTING_KEYS or k in FREE_KEYS}


_Q_RENDER_BLOCKED_COUNT = f"""
    SELECT count(DISTINCT q.request_key) AS n FROM publishing_queue q WHERE {_RENDER_BLOCKED_PREDICATE.format(q='q.')}
"""


@get('/admin/growth/removals', limiter=growth_limit)
def get_growth_removals():
    _gate()
    pending = request.args.get('pending') in ('1', 'true', 'yes')
    attention = request.args.get('attention') in ('1', 'true', 'yes')
    worker = request.args.get('worker') in ('1', 'true', 'yes')
    with api_tx('read committed') as tx:
        # Task 9 (F12): external_access_enabled is the emergency stop for
        # every outbound platform call, removals (deletions) included -- the
        # tasks stay pending in the database for later, this just refuses to
        # hand them to the worker while the stop is engaged.
        halted = settings(tx).get('external_access_enabled') != 'true'
        rows = [] if halted else tx.execute(_Q_REMOVALS, dict(pending=pending, attention=attention,
                                                          worker=worker)).fetchall()
        # Both counts are read even under the stop, and deliberately: they
        # are plain database reads, and a stop that has been engaged for a
        # while is exactly when a growing removal backlog or a pile of
        # undeleted artwork must stay visible rather than disappear with the
        # task list (Wave 2 Task 5, F09).
        overdue = overdue_removals(tx)
        outstanding_cleanup = outstanding_jobs(tx)
        # Fix wave item 3: an abandoned job is left in the table and nothing
        # sweeps it up, and the retention sweep no longer re-queues its key
        # either, so this count is the only thing that surfaces it. Read under
        # the stop for the same reason as the two above.
        abandoned_cleanup = abandoned_jobs(tx)
        # Task 7 (Wave 3b): the rows behind that count -- which object keys
        # are stuck, not just how many -- so an operator does not need a
        # database client to find one. Read under the same stop, for the
        # same reason as the count.
        abandoned = abandoned_job_rows(tx)
        # Wave 3c task 1: `abandoned` above is capped at 50 rows while
        # `abandoned_cleanup` is the uncapped total, so an operator reading
        # only the list has no way to tell it is partial. Additive: the
        # admin Growth tab is deployed separately.
        abandoned_truncated = abandoned_cleanup > len(abandoned)
        # Fix round 1 ruling 2: same reasoning -- a render stuck behind a
        # parked sibling is exactly the kind of backlog that must stay
        # visible under the stop, not disappear along with the task list.
        render_blocked = int(tx.execute(_Q_RENDER_BLOCKED_COUNT).fetchone()['n'])
    return jsonify({'tasks': [_row(r) for r in rows], 'halted': halted,
                    'overdue': overdue, 'outstanding_cleanup': outstanding_cleanup,
                    'abandoned_cleanup': abandoned_cleanup,
                    'abandoned': [_row(r) for r in abandoned],
                    'abandoned_truncated': abandoned_truncated,
                    'render_blocked': render_blocked})


@post('/admin/growth/removals/<int:removal_id>/failed', limiter=growth_limit)
def post_growth_removal_failed(removal_id: int):
    """Task 6 (F09 part 3): the worker reports a removal attempt it could not
    complete, other than the documented missing-target pair (that case still
    goes to /done -- the target is already gone). Every report backs the task
    off (`cleanup.BACKOFF_SECONDS`, indexed by the attempt just recorded) and
    appends to its evidence trail; a permission failure additionally flips the
    reason so the worker stops retrying it and a human sees it instead
    (`?attention=1`)."""
    s = _gate()
    body = _body()
    error = body.get('error')
    code = body.get('code')
    subcode = body.get('subcode')
    permission = bool(body.get('permission'))
    # Fix round 1 ruling 3: code/subcode ride into a jsonb column via an
    # explicit ::int cast below, so anything that is not an int or null is
    # rejected here rather than surfacing as an opaque database error.
    if not _is_int_or_none(code) or not _is_int_or_none(subcode):
        return dict(error='bad_request'), 400
    with api_tx() as tx:
        # Fix round 1 ruling 3: FOR UPDATE so a concurrent report against the
        # same task (two worker retries racing) serialises on this row rather
        # than both reading the same `attempts` and computing the same
        # backoff from it.
        row = tx.execute(
            "SELECT attempts, reason FROM spotlight_removal_task WHERE id = %(i)s AND done_at IS NULL FOR UPDATE",
            dict(i=removal_id)).fetchone()
        if not row:
            abort(404)
        attempts = row['attempts'] + 1
        backoff = cleanup.BACKOFF_SECONDS[min(attempts, len(cleanup.BACKOFF_SECONDS)) - 1]
        reason = 'needs_attention' if permission else row['reason']
        updated = tx.execute(
            """UPDATE spotlight_removal_task
                  SET attempts = %(attempts)s,
                      last_error = %(error)s,
                      -- The migration's default `{}` is not an array; the first
                      -- write to a task's evidence starts the trail fresh.
                      evidence = (CASE WHEN jsonb_typeof(evidence) = 'array' THEN evidence ELSE '[]'::jsonb END)
                                 || jsonb_build_array(jsonb_build_object(
                                        'at', NOW(), 'code', %(code)s::int, 'subcode', %(subcode)s::int,
                                        'error', %(error)s::text)),
                      next_attempt_at = NOW() + make_interval(secs => %(backoff)s),
                      reason = %(reason)s
                -- Fix round 1 ruling 3: `done_at IS NULL` repeated here (not
                -- just in the SELECT above) so this write stays a no-op, by
                -- its own WHERE and not only the row lock, on a task that is
                -- already done.
                WHERE id = %(i)s AND done_at IS NULL
              RETURNING attempts, next_attempt_at, reason""",
            dict(i=removal_id, attempts=attempts, error=error, code=code, subcode=subcode,
                 backoff=backoff, reason=reason)).fetchone()
        if not updated:
            abort(404)
        _audit(tx, s, 'growth.removal.failed', removal_id=removal_id, attempts=updated['attempts'],
               reason=updated['reason'], permission=permission)
    return dict(ok=True, attempts=updated['attempts'], next_attempt_at=_plain(updated['next_attempt_at']),
                reason=updated['reason'])


@post('/admin/growth/removals/<int:removal_id>/done', limiter=growth_limit)
def post_growth_removal_done(removal_id: int):
    s = _gate()
    with api_tx() as tx:
        row = tx.execute(
            """SELECT q.image_key FROM spotlight_removal_task t
                 JOIN publishing_queue q ON q.id = t.queue_id
                WHERE t.id = %(i)s""",
            dict(i=removal_id)).fetchone()
        updated = tx.execute(
            "UPDATE spotlight_removal_task SET done_at = NOW() WHERE id = %(i)s AND done_at IS NULL",
            dict(i=removal_id)).rowcount
        # Wave 2 Task 5 (F09): the key is NOT cleared here and nothing is
        # deleted here. A cleanup job is enqueued in this same transaction,
        # so it commits with the task being marked done; the key stays on the
        # row until the cleanup batch has storage's confirmation that the
        # object is really gone. Clearing it first is what used to leave
        # artwork in the bucket that nothing in the database named any more.
        if updated and row and row['image_key']:
            enqueue_asset_delete(tx, row['image_key'])
        _audit(tx, s, 'growth.removal.done', removal_id=removal_id)
    return dict(ok=True, updated=updated)


@post('/admin/growth/token-health', limiter=growth_limit)
def post_growth_token_health():
    s = _gate()
    body = _body()
    raw_expires = body.get('expires_at')
    if raw_expires is not None and not isinstance(raw_expires, str):
        abort(400)
    # Parsed with the same helper GET uses, and stored normalised, so the
    # read side can never trip over a value the write side accepted.
    parsed = _parse_dt(raw_expires)
    expires_at = parsed.isoformat() if parsed is not None else ''
    valid = body.get('valid')
    if not isinstance(valid, bool):
        abort(400)
    with api_tx() as tx:
        set_setting(tx, 'token_expires_at', expires_at)
        set_setting(tx, 'token_valid', 'true' if valid else 'false')
        _audit(tx, s, 'growth.token.health', valid=valid, expires_at=expires_at)
    return dict(ok=True, expires_at=expires_at or None, valid=valid)


# ---------------------------------------------------------------------------
# Admin-only endpoints
# ---------------------------------------------------------------------------

@apost('/admin/growth/settings')
def post_growth_settings(s: t.SessionInfo):
    require_admin(s)
    body = _body()
    key = body.get('key')
    value = body.get('value')
    with api_tx() as tx:
        try:
            set_setting(tx, key, value)
        except ValueError as e:
            abort(400, str(e))
        _audit(tx, s, 'growth.settings.set', key=key, value=value)
    return dict(ok=True, key=key, value=value)


@apost('/admin/growth/queue/<qid>/approve')
def post_growth_queue_approve(s: t.SessionInfo, qid: str):
    """Approves ONE platform row (its sibling is approved separately, in its
    own call, when it in turn is ready). The object behind this row's own
    image_key is private until this point (Wave 2 F09); it is only made
    public here, AFTER the status/scheduling transaction commits, so a
    Spaces call never runs inside a transaction.

    Fix round 1 (ruling 1): a storage failure at that point reverts this row
    alone back to `review` in a second, short transaction -- but ONLY when
    it is still sitting where this call left it (`status = 'scheduled'`).
    The upload/make_public round trip is a real gap in time; the row can be
    claimed into `processing` (or, rarely, already `published`) by the
    worker in between. Reverting THAT row to `review` would rewrite a
    status the worker itself now owns, out from under it. When the revert's
    own WHERE matches zero rows, nothing is touched, the answer carries
    `in_flight: true`, and the audit action says so (`...storage_failed_in_flight`)
    instead of `...reverted`."""
    require_admin(s)
    queue_id = _qid(qid)
    requested = _parse_dt(_body().get('scheduled_for'))
    with api_tx() as tx:
        row = tx.execute("SELECT scheduled_for, image_key FROM publishing_queue WHERE id = %(id)s",
                         dict(id=queue_id)).fetchone()
        if not row:
            abort(404)
        # An explicit slot wins; otherwise keep the slot member-of-the-week
        # already stored on the row; otherwise fall to the next default slot.
        when = requested or row['scheduled_for'] or _default_slot()
        try:
            set_status(tx, queue_id, 'scheduled')
        except ValueError as e:
            abort(409, str(e))
        tx.execute(
            "UPDATE publishing_queue SET scheduled_for = %(w)s, updated_at = NOW() WHERE id = %(id)s",
            dict(w=when, id=queue_id))
        _audit(tx, s, 'growth.queue.approve', queue_id=str(queue_id),
               scheduled_for=when.isoformat())
    if row['image_key']:
        try:
            st.make_public(row['image_key'])
        except Exception:
            with api_tx() as tx:
                cur = tx.execute(
                    """UPDATE publishing_queue SET status = 'review', scheduled_for = NULL, updated_at = NOW()
                        WHERE id = %(id)s AND status = 'scheduled'""",
                    dict(id=queue_id))
                in_flight = not cur.rowcount
                if in_flight:
                    _audit(tx, s, 'growth.queue.approve.storage_failed_in_flight', queue_id=str(queue_id))
                else:
                    _audit(tx, s, 'growth.queue.approve.reverted', queue_id=str(queue_id))
            return dict(error='storage_unavailable', in_flight=in_flight), 503
    return dict(status='scheduled', scheduled_for=when.isoformat())


@apost('/admin/growth/queue/<qid>/cancel')
def post_growth_queue_cancel(s: t.SessionInfo, qid: str):
    require_admin(s)
    queue_id = _qid(qid)
    reason = _body().get('reason') or 'admin_cancel'
    with api_tx() as tx:
        try:
            set_status(tx, queue_id, 'cancelled', error=reason)
        except ValueError as e:
            abort(409, str(e))
        _audit(tx, s, 'growth.queue.cancel', queue_id=str(queue_id), reason=reason)
    return dict(status='cancelled')


@apost('/admin/growth/queue/<qid>/retry')
def post_growth_queue_retry(s: t.SessionInfo, qid: str):
    require_admin(s)
    queue_id = _qid(qid)
    with api_tx() as tx:
        # failed -> scheduled. The attempts cap lives in set_status (Task 2).
        try:
            set_status(tx, queue_id, 'scheduled')
        except ValueError as e:
            abort(409, str(e))
        _audit(tx, s, 'growth.queue.retry', queue_id=str(queue_id))
    return dict(status='scheduled')


@apost('/admin/growth/queue/<qid>/reschedule')
def post_growth_queue_reschedule(s: t.SessionInfo, qid: str):
    require_admin(s)
    queue_id = _qid(qid)
    when = _parse_dt(_body().get('scheduled_for'))
    if when is None:
        abort(400)
    with api_tx() as tx:
        cur = tx.execute(
            """UPDATE publishing_queue SET scheduled_for = %(w)s, updated_at = NOW()
                WHERE id = %(id)s AND status = 'scheduled'""",
            dict(w=when, id=queue_id))
        if not cur.rowcount:
            abort(409)
        _audit(tx, s, 'growth.queue.reschedule', queue_id=str(queue_id),
               scheduled_for=when.isoformat())
    return dict(status='scheduled', scheduled_for=when.isoformat())


@apost('/admin/growth/queue/<qid>/caption')
def post_growth_queue_caption(s: t.SessionInfo, qid: str):
    require_admin(s)
    queue_id = _qid(qid)
    caption = _body().get('caption')
    if not isinstance(caption, str) or not caption.strip():
        abort(400)
    with api_tx() as tx:
        row = tx.execute(
            "SELECT request_key FROM publishing_queue WHERE id = %(id)s",
            dict(id=queue_id)).fetchone()
        if not row:
            abort(409)
        # edit_caption (Task 2) is the Phase B logic moved into
        # service.spotlight.revisions: it preserves the request's one /s/
        # campaign link and creates a new revision rather than editing the
        # current one, so a caption change never silently alters content a
        # member already consented to. It raises in_flight for a
        # scheduled/processing row and terminal for a published/cancelled
        # one -- either is a real conflict, reported as JSON, not aborted.
        try:
            edit_caption(tx, row['request_key'], caption, _actor(s))
        except ValueError as e:
            return dict(error=str(e)), 409
        stored = tx.execute(
            "SELECT caption FROM publishing_queue WHERE id = %(id)s",
            dict(id=queue_id)).fetchone()['caption']
        _audit(tx, s, 'growth.queue.caption', queue_id=str(queue_id))
    return dict(ok=True, caption=stored)


@apost('/admin/growth/queue/<qid>/reconcile')
def post_growth_queue_reconcile(s: t.SessionInfo, qid: str):
    """The operator's answer for a row whose delivery was never resolved
    (spec 5, fix wave item 1).

    A row reaped out of `processing` when its lease expired, or parked by a
    `delivery_unknown` receipt, may have a LIVE post behind it that no lease
    holder will ever come back to confirm. A human checks the page and
    reports the external id here. Admin session only: this is a judgement,
    not something the unattended worker may make, so it is registered with
    `apost` and never reachable with the cron secret.

    From there it behaves exactly like a `published` receipt: the removal
    task is filed when the member has already withdrawn, the occurrence is
    recorded, and E5 fires under the same conditions the complete route
    applies.

    Residual fix (item 1): the lock taken here covers every row of the
    request key, the same as the complete route above, before
    `reconcile_published` is ever called -- see `lock_request_rows`'s
    docstring in service/spotlight/queue.py for why.
    """
    require_admin(s)
    body = _body()
    external_post_id = body.get('external_post_id')
    post_url = body.get('post_url')
    if not external_post_id or not isinstance(external_post_id, str):
        abort(400)
    queue_id = _qid(qid)
    with api_tx() as tx:
        rows = lock_request_rows(tx, queue_id)
        row = next((r for r in rows if r['id'] == queue_id), None)
        if not row:
            abort(404)
        try:
            reconcile_published(tx, row, external_post_id, post_url)
        except ValueError as e:
            return dict(error=str(e)), 409
        occurrences = record_occurrence(tx, row['kind'], row['request_key'], pictured_people(tx, row))
        first_confirmation = is_first_confirmation(tx, row['request_key'])
        if (first_confirmation and row['subject_person_id'] is not None
                and row['cancellation_requested_at'] is None):
            live = (row['subject_person_id'], row['request_key'], external_post_id,
                    row['platform'], post_url)
            # Inside the transaction on purpose (F07), same as the complete
            # route: the queued email commits with the reconciliation, and a
            # failure to queue it is audited rather than allowed to undo the
            # operator's reconciliation of a post that is genuinely live.
            enqueue_error = _enqueue_card_live(tx, *live)
            if enqueue_error is not None:
                _audit(tx, s, 'growth.email.enqueue_failed', campaign='e5',
                       request_key=row['request_key'], error=enqueue_error)
        _audit(tx, s, 'growth.queue.reconcile', queue_id=str(queue_id),
               external_post_id=external_post_id, occurrences=occurrences,
               first_confirmation=first_confirmation)
    return dict(ok=True, status='published', external_post_id=external_post_id)


@apost('/admin/growth/queue/purge')
def post_growth_queue_purge(s: t.SessionInfo):
    """The break-glass switch: stop everything that has not gone out yet.

    Fix wave item 2: purge stops short of the rows withdrawal itself will not
    cancel. A `processing` row belongs to its lease holder, and a `review` row
    whose delivery was never resolved may already be live on the platform --
    cancelling either would orphan a post nothing would then remove. Both are
    stamped with `cancellation_requested_at` instead, which is exactly what
    makes the lease holder's own late receipt (or an operator's reconcile)
    file the removal task. Every other non-terminal row is cancelled outright,
    from any status, which is why this is a direct UPDATE rather than
    `set_status` per row.
    """
    require_admin(s)
    with api_tx() as tx:
        stamped = tx.execute(
            """UPDATE publishing_queue
                   SET cancellation_requested_at = COALESCE(cancellation_requested_at, NOW()),
                       updated_at = NOW()
                 WHERE status NOT IN ('published', 'cancelled')""").rowcount
        n = tx.execute(
            """UPDATE publishing_queue SET status = 'cancelled', error = 'purged', updated_at = NOW()
                WHERE status NOT IN ('published', 'cancelled', 'processing')
                  AND NOT (status = 'review'
                           AND delivery_state IN ('attempting', 'delivery_unknown'))""").rowcount
        left_attempting = stamped - n
        _audit(tx, s, 'growth.queue.purge', cancelled=n, left_attempting=left_attempting)
    return dict(cancelled=n, left_attempting=left_attempting)


@apost('/admin/growth/spotlight/member-of-week')
def post_growth_spotlight_member_of_week(s: t.SessionInfo):
    require_admin(s)
    body = _body()
    person_id = body.get('person_id')
    if not person_id:
        abort(400)
    when = _parse_dt(body.get('scheduled_for'))
    with api_tx() as tx:
        # Task 9 (F12): invites_enabled gates candidate creation and E4.
        if settings(tx).get('invites_enabled') != 'true':
            return dict(error='invites_paused'), 409
        info = tx.execute(
            """SELECT split_part(name, ' ', 1) AS first_name,
                      date_part('year', age(date_of_birth))::int AS age,
                      COALESCE(country, location_short_friendly) AS country
                 FROM person WHERE id = %(p)s""",
            dict(p=person_id)).fetchone()
        if not info:
            abort(404)
        caption = body.get('caption') or _member_of_week_caption(
            info['first_name'], info['age'], info['country'])
        try:
            rk = create_candidate(tx, kind='member_of_week', subject_person_id=person_id,
                                  caption=caption, created_by=_actor(s))
        except ValueError as e:
            abort(400, str(e))
        if when is not None:
            # Stored now so approve can keep the admin's chosen slot.
            tx.execute(
                "UPDATE publishing_queue SET scheduled_for = %(w)s WHERE request_key = %(rk)s",
                dict(w=when, rk=rk))
        # Fix wave item 4: the same gate the welcome route carries. A
        # member must never be invited to approve a card they cannot act
        # on, so with approvals off the candidate is created (ready the
        # moment approvals resume) but E4 is withheld.
        invite_sent = settings(tx).get('approvals_enabled') == 'true'
        if invite_sent:
            # Inside the transaction on purpose (F07), same as the welcome
            # route above, and a failure propagates for the same reason.
            _enqueue_card_ready(tx, person_id, rk)
        _audit(tx, s, 'growth.queue.member_of_week', person_id=person_id, request_key=rk,
               invite_sent=invite_sent)
    return dict(request_key=rk)


@aget('/admin/growth/spotlight/suggest')
def get_growth_spotlight_suggest(s: t.SessionInfo):
    require_admin(s)
    out = []
    with api_tx('read committed') as tx:
        last = tx.execute(_Q_LAST_FEATURED_GENDER).fetchone()
        rows = tx.execute(_Q_SUGGEST, dict(recent=last['gender'] if last else None)).fetchall()
        for r in rows:
            ok, _reason = eligibility(tx, r['id'])
            if not ok:
                continue
            uuid_value = primary_photo_uuid(tx, r['id'])
            out.append(dict(
                person_id=r['id'],
                first_name=r['first_name'],
                age=r['age'],
                country=r['country'],
                photo_url=photo_url(uuid_value) if uuid_value else None,
                reason=_suggest_reason(r['spotlight_last_featured_at']),
                suggested_caption=_member_of_week_caption(r['first_name'], r['age'], r['country']),
            ))
            if len(out) == 3:
                break
    return jsonify(out)


@aget('/admin/growth/token-health')
def get_growth_token_health(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        cfg = settings(tx)
    expires_at = cfg.get('token_expires_at') or None
    valid = cfg.get('token_valid') == 'true'
    days_left = None
    if expires_at:
        parsed = _parse_dt(expires_at)
        if parsed is not None:
            days_left = (parsed - datetime.now(timezone.utc)).days
    return dict(expires_at=expires_at, valid=valid, days_left=days_left)


# ---------------------------------------------------------------------------
# Default captions (sentence case, no em dashes)
# ---------------------------------------------------------------------------

def _roundup_caption(count: int, countries: int) -> str:
    if count > 0:
        return f'New this week on Ahavah. {count} joined from {countries} countries.'
    return 'New this week on Ahavah.'


def _welcome_caption(first_name: str, country: Optional[str]) -> str:
    if country:
        return f'Welcome to Ahavah, {first_name}. {country}.'
    return f'Welcome to Ahavah, {first_name}.'


def _member_of_week_caption(first_name: str, age, country: Optional[str]) -> str:
    if country:
        return f'Member of the week: {first_name}, {age}, {country}.'
    return f'Member of the week: {first_name}, {age}.'


def _suggest_reason(last_featured: Optional[datetime]) -> str:
    if last_featured is None:
        return 'never featured'
    days = (datetime.now(timezone.utc) - last_featured).days
    return f'last featured {days} days ago'
