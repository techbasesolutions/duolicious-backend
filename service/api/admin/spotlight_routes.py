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
`_session()`, `require_admin()`, object-store uploads and the outbound E4/E5
sends all happen strictly outside the handler's own transaction.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid as uuid_mod
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import duotypes as t
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
                                     set_status, settings, set_setting,
                                     reap_expired_leases, record_receipt, OUTCOMES,
                                     OUTCOME_DELIVERY_STATE, PLATFORMS,
                                     _SETTING_KEYS, _FREE_KEYS)
from service.spotlight.revisions import attach_render, consent_complete, create_revision, edit_caption
from service.spotlight.roundup import roundup_snapshot
from service.spotlight.storage import _bucket, delete_images

_PNG_SIG = b'\x89PNG\r\n\x1a\n'


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
# Side effects (object store + the E4/E5 emails Task 4 lands)
# ---------------------------------------------------------------------------

def _put_png(key: str, data: bytes) -> None:
    """Upload one rendered card through the same bucket resolver
    `service.spotlight.storage.delete_images` uses, so upload and delete
    share one set of credentials and one endpoint override."""
    _bucket().put_object(Key=key, Body=data, ACL='public-read', ContentType='image/png')


def _send_card_ready(person_id: int, request_key: str) -> None:
    try:
        from emails.spotlight_card_ready import send_card_ready_async
    except ImportError:
        print(f'E4 not available yet; card ready for person {person_id} key {request_key}')
        return
    send_card_ready_async(person_id, request_key)


def _send_card_live(person_id: int, request_key: str, external_post_id: str, platform: str,
                     post_url: Optional[str] = None) -> None:
    try:
        from emails.spotlight_card_live import send_card_live_async
    except ImportError:
        print(f'E5 not available yet; card live for person {person_id} key {request_key}')
        return
    send_card_live_async(person_id, request_key, external_post_id, platform, post_url)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

# Clicks and sign-ups are per REQUEST (both platform rows share the campaign
# link), counted off campaign_link.kind = 'post:<request_key>' and excluding
# the bot user-agent class so previews and crawlers don't inflate the number.
# q.member_approved_at and q.approved_photo_uuid (the pre-Wave-1 columns) are
# left untouched but no longer written by anything -- both are now sourced
# from the current revision and its consent row instead (Task 2).
_Q_ROWS = """
    SELECT q.id::text AS id,
           q.request_key,
           q.kind,
           q.platform,
           q.status,
           q.caption,
           q.image_url,
           q.scheduled_for,
           q.attempts,
           q.external_post_id,
           q.error,
           q.current_revision_id,
           r.revision AS revision_number,
           r.asset_hash AS revision_asset_hash,
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
     ORDER BY q.created_at DESC
     LIMIT 200
"""

# Task 9 (F12): the welcome cohort is defined by sign-up time, not opt-in
# time -- opt-in can happen long after signing up (or be re-toggled), so
# ordering by it let a member who opted in years ago resurface as "new".
_Q_WELCOME_CANDIDATES = """
    SELECT p.id, p.name
      FROM person p
     WHERE p.sign_up_time > NOW() - interval '14 days'
       AND p.spotlight_opt_in IS TRUE
       AND NOT EXISTS (SELECT 1 FROM publishing_queue q
                        WHERE q.subject_person_id = p.id AND q.kind = 'welcome')
     ORDER BY p.sign_up_time
     LIMIT 50
"""

# Task 9 (F12): a duplicate tick firing twice in the same week used to see no
# roundup "recent enough" (a 6-day lookback measured from each call's own
# NOW()) and create two. `_week_key` is the same business key
# `post_growth_spotlight_roundup` passes to `create_candidate`, so this is
# now an existence check on this week's key, not a rolling window.
_Q_ROUNDUP_RECENT = """
    SELECT EXISTS (SELECT 1 FROM publishing_queue WHERE request_key = %(rk)s) AS recent
"""


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

_Q_REMOVALS = """
    SELECT t.id,
           t.queue_id::text AS queue_id,
           t.platform,
           t.external_post_id,
           t.reason,
           t.done_at,
           t.created_at,
           q.request_key,
           q.kind,
           q.caption
      FROM spotlight_removal_task t
      LEFT JOIN publishing_queue q ON q.id = t.queue_id
     WHERE (%(pending)s::bool IS NOT TRUE OR t.done_at IS NULL)
     ORDER BY t.created_at DESC
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
    """
    s = _gate()
    body = _body()
    status = body.get('status')
    lease_token = body.get('lease_token')
    external_post_id = body.get('external_post_id')
    post_url = body.get('post_url')
    error = body.get('error')
    queue_id = _qid(qid)
    live = None
    occurrences = None
    first_confirmation = None
    with api_tx() as tx:
        row = tx.execute(
            """SELECT request_key, platform, subject_person_id, cancellation_requested_at, kind
                 FROM publishing_queue WHERE id = %(id)s""",
            dict(id=queue_id)).fetchone()
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
    # Outside the transaction on purpose: the mail path opens its own api_tx.
    if live is not None:
        _send_card_live(*live)
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
    # mislabelled or truncated upload must not leave a half-written key behind.
    try:
        data = base64.b64decode(body.get('png_base64') or '', validate=True)
    except Exception:
        abort(400)
    if not data.startswith(_PNG_SIG):
        abort(400)

    key = f'spotlight/{request_key}-{platform}.png'
    url = f'{USER_IMAGES_BASE_URL}/spotlight/{request_key}-{platform}.png'

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

    # Upload outside any transaction: a network round trip must not hold the
    # api connection lock.
    _put_png(key, data)

    # The status was read before the upload, and the upload is a network round
    # trip: an approve or a cancel can land in between. The write repeats the
    # check as part of its own WHERE, so the row is only stamped if it is still
    # in a state that may have its artwork replaced. Zero rows updated means it
    # moved, and the answer is the same 409 the pre-check gives.
    moved = False
    with api_tx() as tx:
        cur = tx.execute(
            """UPDATE publishing_queue SET image_key = %(k)s, image_url = %(u)s, updated_at = NOW()
                WHERE request_key = %(rk)s AND platform = %(pl)s
                  AND status IN ('awaiting_member', 'awaiting_render', 'review')""",
            dict(k=key, u=url, rk=request_key, pl=platform))
        if not cur.rowcount:
            moved = True
        else:
            rows = tx.execute(
                """SELECT platform, image_key, image_url, current_revision_id
                     FROM publishing_queue WHERE request_key = %(rk)s""",
                dict(rk=request_key)).fetchall()
            if rows and all(r['image_url'] for r in rows):
                # One render per revision (Task 2): the platform image that
                # completes the set stamps the revision's asset_hash/image
                # columns, once. Pinned to the facebook row's own upload when
                # one exists, so the member's preview never depends on which
                # platform happened to finish uploading last -- this call may
                # be for instagram, completing a set whose facebook image was
                # uploaded by an earlier, separate call, so facebook's raw
                # bytes are not in hand here; asset_hash is a one-shot dedup
                # marker, not a content hash of pixel bytes, so hashing the
                # pinned row's own storage key keeps it deterministic and
                # available regardless of upload order. Each row's own
                # image_key/image_url was already stamped by the per-platform
                # UPDATE above (this call's own row) or by an earlier call
                # (the other platform's row), so nothing further needs
                # restoring here.
                pinned = next((r for r in rows if r['platform'] == 'facebook'), rows[0])
                rev_id = pinned['current_revision_id']
                asset_hash = hashlib.sha256(pinned['image_key'].encode()).hexdigest()
                attach_render(tx, rev_id, asset_hash, pinned['image_key'], pinned['image_url'])
                # A roundup (no subject) row is ready to schedule as soon as
                # it is rendered. A subject row stays `awaiting_member` even
                # once rendered -- approve_card is what moves it on to
                # review, and only once the member actually consents.
                tx.execute(
                    """UPDATE publishing_queue SET status = 'review', updated_at = NOW()
                        WHERE request_key = %(rk)s AND status = 'awaiting_render'""",
                    dict(rk=request_key))
            _audit(tx, s, 'growth.queue.image', request_key=request_key, platform=platform)
    if moved:
        return dict(error='bad_status'), 409
    return dict(image_url=url)


@get('/admin/growth/candidates', limiter=growth_limit)
def get_growth_candidates():
    _gate()
    with api_tx('read committed') as tx:
        # Task 9 (F12): invites off means no new welcome or roundup should
        # even be suggested, not just refused on creation.
        if settings(tx).get('invites_enabled') != 'true':
            return dict(welcomes=[], roundup_due=False, invites_enabled=False)
        rows = tx.execute(_Q_WELCOME_CANDIDATES).fetchall()
        welcomes = []
        for r in rows:
            ok, _reason = eligibility(tx, r['id'])
            if ok:
                welcomes.append(dict(person_id=r['id'], first_name=(r['name'] or '').split(' ')[0]))
        recent_roundup = tx.execute(_Q_ROUNDUP_RECENT, dict(rk=_week_key())).fetchone()['recent']
    # Monday is weekday() == 0.
    roundup_due = datetime.now(timezone.utc).weekday() == 0 and not recent_roundup
    return dict(welcomes=welcomes, roundup_due=roundup_due, invites_enabled=True)


@post('/admin/growth/spotlight/welcome', limiter=growth_limit)
def post_growth_spotlight_welcome():
    s = _gate()
    person_id = _body().get('person_id')
    if not person_id:
        abort(400)
    with api_tx() as tx:
        cfg = settings(tx)
        # Task 9 (F12): invites_enabled gates candidate creation and E4.
        if cfg.get('invites_enabled') != 'true':
            return dict(error='invites_paused'), 409
        already = tx.execute(
            """SELECT 1 FROM publishing_queue
                WHERE subject_person_id = %(p)s AND kind = 'welcome' LIMIT 1""",
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
        _audit(tx, s, 'growth.queue.welcome', person_id=person_id, request_key=rk, invite_sent=invite_sent)
    if invite_sent:
        _send_card_ready(person_id, rk)
    return dict(request_key=rk)


@post('/admin/growth/spotlight/roundup', limiter=growth_limit)
def post_growth_spotlight_roundup():
    s = _gate()
    week_key = _week_key()
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
            caption_row = tx.execute(
                "SELECT caption FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1",
                dict(rk=rk)).fetchone()
            create_revision(
                tx, rk, caption=caption_row['caption'], photo_uuid=None,
                participants=[dict(person_id=tile['person_id'], first_name=tile['first_name'],
                                   photo_url=tile['photo_url']) for tile in snapshot['tiles']],
                # Fix round 1 (ruling 2): the same PLATFORMS constant
                # create_candidate inserted rows with, not an unordered
                # SELECT over those rows.
                channels=list(PLATFORMS), created_by=_actor(s))
        _audit(tx, s, 'growth.queue.roundup', request_key=rk,
               tiles=len(snapshot['tiles']), count=snapshot['count'], count_only=count_only)
    return dict(request_key=rk)


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
    return {k: v for k, v in cfg.items() if k in _SETTING_KEYS or k in _FREE_KEYS}


@get('/admin/growth/removals', limiter=growth_limit)
def get_growth_removals():
    _gate()
    pending = request.args.get('pending') in ('1', 'true', 'yes')
    with api_tx('read committed') as tx:
        # Task 9 (F12): external_access_enabled is the emergency stop for
        # every outbound platform call, removals (deletions) included -- the
        # tasks stay pending in the database for later, this just refuses to
        # hand them to the worker while the stop is engaged.
        halted = settings(tx).get('external_access_enabled') != 'true'
        rows = [] if halted else tx.execute(_Q_REMOVALS, dict(pending=pending)).fetchall()
    return jsonify({'tasks': [_row(r) for r in rows], 'halted': halted})


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
        if updated and row and row['image_key']:
            tx.execute(
                """UPDATE publishing_queue SET image_key = NULL, image_url = NULL
                    WHERE id = (SELECT queue_id FROM spotlight_removal_task WHERE id = %(i)s)""",
                dict(i=removal_id))
        _audit(tx, s, 'growth.removal.done', removal_id=removal_id)
    # Storage is best-effort and outside the transaction's success/failure:
    # a Spaces error here must never undo the removal task being marked done.
    if updated and row and row['image_key']:
        delete_images([row['image_key']])
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
    require_admin(s)
    queue_id = _qid(qid)
    requested = _parse_dt(_body().get('scheduled_for'))
    with api_tx() as tx:
        row = tx.execute("SELECT scheduled_for FROM publishing_queue WHERE id = %(id)s",
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


@apost('/admin/growth/queue/purge')
def post_growth_queue_purge(s: t.SessionInfo):
    require_admin(s)
    with api_tx() as tx:
        # Deliberately a direct UPDATE rather than set_status per row: purge is
        # the break-glass switch and must cancel from ANY non-terminal state,
        # including `processing`, which the normal transition table forbids.
        cur = tx.execute(
            """UPDATE publishing_queue SET status = 'cancelled', error = 'purged', updated_at = NOW()
                WHERE status NOT IN ('published', 'cancelled')""")
        n = cur.rowcount
        _audit(tx, s, 'growth.queue.purge', cancelled=n)
    return dict(cancelled=n)


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
        _audit(tx, s, 'growth.queue.member_of_week', person_id=person_id, request_key=rk)
    _send_card_ready(person_id, rk)
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
