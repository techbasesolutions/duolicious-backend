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
import uuid as uuid_mod
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import duotypes as t
from flask import abort, jsonify, request

from database import api_tx
from duohash import sha512
from service.admin import record_audit, require_admin
from service.api.cron_auth import require_admin_or_cron, is_cron_request
from service.api.decorators import aget, apost, get, post, Q_GET_SESSION
from service.config import USER_IMAGES_BASE_URL
from service.spotlight.eligibility import eligibility, primary_photo_uuid, photo_url
from service.spotlight.queue import (create_candidate, expire_member_approvals, attach_image,
                                     set_status, settings, set_setting, stamp_featured, PLATFORMS)

_PNG_SIG = b'\x89PNG\r\n\x1a\n'

_ROUNDUP_CAPTION = 'New this week on Ahavah.'


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
    if not row:
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
    """Resolve + authorise in one call. Both happen before any api_tx opens."""
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
    Cron has no actor to attribute, so it logs a line instead."""
    if s is not None and not is_cron_request():
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
    """Upload one rendered card. `bucket` is the module-level Spaces bucket
    that `service.person.put_image_in_object_store` writes through; reusing it
    keeps one set of credentials and one endpoint override."""
    from service.person import bucket
    bucket.put_object(Key=key, Body=data, ACL='public-read', ContentType='image/png')


def _send_card_ready(person_id: int, request_key: str) -> None:
    try:
        from emails.spotlight_card_ready import send_card_ready_async
    except ImportError:
        print(f'E4 not available yet; card ready for person {person_id} key {request_key}')
        return
    send_card_ready_async(person_id, request_key)


def _send_card_live(person_id: int, request_key: str, external_post_id: str, platform: str) -> None:
    try:
        from emails.spotlight_card_live import send_card_live_async
    except ImportError:
        print(f'E5 not available yet; card live for person {person_id} key {request_key}')
        return
    send_card_live_async(person_id, request_key, external_post_id, platform)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

# Clicks and sign-ups are per REQUEST (both platform rows share the campaign
# link), counted off campaign_link.kind = 'post:<request_key>' and excluding
# the bot user-agent class so previews and crawlers don't inflate the number.
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
           q.member_approved_at,
           q.subject_person_id,
           split_part(p.name, ' ', 1) AS first_name,
           date_part('year', age(p.date_of_birth))::int AS age,
           COALESCE(p.country, p.location_short_friendly) AS country,
           COALESCE(
               q.approved_photo_uuid::text,
               (SELECT ph.uuid::text FROM photo ph
                 WHERE ph.person_id = q.subject_person_id
                   AND ph.moderation_status = 'approved'
                 ORDER BY ph.position LIMIT 1)) AS photo_uuid,
           (SELECT count(*) FROM campaign_click c
              JOIN campaign_link l ON l.key = c.link_key
             WHERE l.kind = 'post:' || q.request_key
               AND c.ua_class <> 'bot')::int AS clicks,
           (SELECT count(*) FROM campaign_click c
              JOIN campaign_link l ON l.key = c.link_key
             WHERE l.kind = 'post:' || q.request_key
               AND c.ua_class <> 'bot'
               AND c.signup_person_id IS NOT NULL)::int AS signups
      FROM publishing_queue q
      LEFT JOIN person p ON p.id = q.subject_person_id
     WHERE (%(status)s::text IS NULL OR q.status = %(status)s::text)
     ORDER BY q.created_at DESC
     LIMIT 200
"""

_Q_WELCOME_CANDIDATES = """
    SELECT p.id, p.name
      FROM person p
     WHERE p.spotlight_opt_in_at > NOW() - interval '14 days'
       AND NOT EXISTS (SELECT 1 FROM publishing_queue q
                        WHERE q.subject_person_id = p.id AND q.kind = 'welcome')
     ORDER BY p.spotlight_opt_in_at
"""

_Q_ROUNDUP_RECENT = """
    SELECT EXISTS (SELECT 1 FROM publishing_queue
                    WHERE kind = 'roundup'
                      AND created_at > NOW() - interval '6 days') AS recent
"""

_Q_LAST_FEATURED_GENDER = """
    SELECT g.name AS gender
      FROM person p
      JOIN gender g ON g.id = p.gender_id
     WHERE p.spotlight_last_featured_at IS NOT NULL
     ORDER BY p.spotlight_last_featured_at DESC
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
           p.spotlight_last_featured_at
      FROM person p
      LEFT JOIN gender g ON g.id = p.gender_id
     WHERE p.spotlight_opt_in IS TRUE
     ORDER BY (g.name IS DISTINCT FROM %(recent)s::text) DESC,
              p.spotlight_last_featured_at ASC NULLS FIRST,
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


def _queue_row(r) -> dict:
    subject = None
    if r['subject_person_id'] is not None:
        subject = dict(
            person_id=r['subject_person_id'],
            first_name=r['first_name'],
            age=r['age'],
            country=r['country'],
            photo_url=photo_url(r['photo_uuid']) if r['photo_uuid'] else None,
        )
    return dict(
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
    )


# ---------------------------------------------------------------------------
# Admin-or-cron endpoints (unauthenticated decorators; see DECORATOR NOTE)
# ---------------------------------------------------------------------------

@get('/admin/growth/queue')
def get_growth_queue():
    _gate()
    status = request.args.get('status') or None
    with api_tx('read committed') as tx:
        rows = tx.execute(_Q_ROWS, dict(status=status)).fetchall()
    return jsonify([_queue_row(r) for r in rows])


@post('/admin/growth/queue/claim')
def post_growth_queue_claim():
    s = _gate()
    try:
        requested = int(_body().get('max') or 2)
    except (TypeError, ValueError):
        abort(400)
    requested = max(1, min(requested, 50))
    with api_tx() as tx:
        # The kill switch. With the scheduler off nothing is leased, so a cron
        # worker left running cannot publish.
        if settings(tx).get('scheduler_enabled') != 'true':
            return jsonify([])
        rows = tx.execute("SELECT * FROM claim_spotlight_posts(%(m)s)", dict(m=requested)).fetchall()
        _audit(tx, s, 'growth.queue.claim', claimed=len(rows))
    return jsonify([_row(r) for r in rows])


@post('/admin/growth/queue/<qid>/complete')
def post_growth_queue_complete(qid: str):
    s = _gate()
    body = _body()
    status = body.get('status')
    if status not in ('published', 'failed', 'review'):
        abort(400)
    queue_id = _qid(qid)
    external_post_id = body.get('external_post_id')
    error = body.get('error')
    live = None
    with api_tx() as tx:
        row = tx.execute(
            "SELECT request_key, platform, subject_person_id FROM publishing_queue WHERE id = %(id)s",
            dict(id=queue_id)).fetchone()
        if not row:
            abort(404)
        try:
            set_status(tx, queue_id, status, external_post_id=external_post_id, error=error)
        except ValueError as e:
            abort(409, str(e))
        if status == 'published' and row['subject_person_id'] is not None:
            stamp_featured(tx, row['subject_person_id'])
            live = (row['subject_person_id'], row['request_key'],
                    external_post_id or '', row['platform'])
        _audit(tx, s, 'growth.queue.complete', queue_id=str(queue_id), status=status)
    # Outside the transaction on purpose: the mail path opens its own api_tx.
    if live is not None:
        _send_card_live(*live)
    return dict(ok=True, status=status)


@get('/admin/growth/queue/<qid>/eligible')
def get_growth_queue_eligible(qid: str):
    _gate()
    queue_id = _qid(qid)
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT kind, subject_person_id FROM publishing_queue WHERE id = %(id)s",
                         dict(id=queue_id)).fetchone()
        if not row:
            abort(404)
        if row['kind'] == 'roundup' or row['subject_person_id'] is None:
            return dict(ok=True, reason='')
        ok, reason = eligibility(tx, row['subject_person_id'])
    return dict(ok=ok, reason=reason)


@post('/admin/growth/queue/<request_key>/image')
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
            "SELECT 1 FROM publishing_queue WHERE request_key = %(rk)s AND platform = %(pl)s",
            dict(rk=request_key, pl=platform)).fetchone()
    if not known:
        abort(404)

    # Upload outside any transaction: a network round trip must not hold the
    # api connection lock.
    _put_png(key, data)

    with api_tx() as tx:
        tx.execute(
            """UPDATE publishing_queue SET image_key = %(k)s, image_url = %(u)s, updated_at = NOW()
                WHERE request_key = %(rk)s AND platform = %(pl)s""",
            dict(k=key, u=url, rk=request_key, pl=platform))
        rows = tx.execute(
            "SELECT platform, image_key, image_url FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=request_key)).fetchall()
        if rows and all(r['image_url'] for r in rows):
            # attach_image is the state transition (awaiting_render -> review);
            # it stamps one key/url across every row of the request, so each
            # platform's own rendered image is written back afterwards.
            attach_image(tx, request_key, key, url)
            for r in rows:
                tx.execute(
                    """UPDATE publishing_queue SET image_key = %(k)s, image_url = %(u)s
                        WHERE request_key = %(rk)s AND platform = %(pl)s""",
                    dict(k=r['image_key'], u=r['image_url'], rk=request_key, pl=r['platform']))
        _audit(tx, s, 'growth.queue.image', request_key=request_key, platform=platform)
    return dict(image_url=url)


@get('/admin/growth/candidates')
def get_growth_candidates():
    _gate()
    with api_tx('read committed') as tx:
        rows = tx.execute(_Q_WELCOME_CANDIDATES).fetchall()
        welcomes = []
        for r in rows:
            ok, _reason = eligibility(tx, r['id'])
            if ok:
                welcomes.append(dict(person_id=r['id'], first_name=(r['name'] or '').split(' ')[0]))
        recent_roundup = tx.execute(_Q_ROUNDUP_RECENT).fetchone()['recent']
    # Monday is weekday() == 0.
    roundup_due = datetime.now(timezone.utc).weekday() == 0 and not recent_roundup
    return dict(welcomes=welcomes, roundup_due=roundup_due)


@post('/admin/growth/spotlight/welcome')
def post_growth_spotlight_welcome():
    s = _gate()
    person_id = _body().get('person_id')
    if not person_id:
        abort(400)
    with api_tx() as tx:
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
        _audit(tx, s, 'growth.queue.welcome', person_id=person_id, request_key=rk)
    _send_card_ready(person_id, rk)
    return dict(request_key=rk)


@post('/admin/growth/spotlight/roundup')
def post_growth_spotlight_roundup():
    s = _gate()
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None,
                              caption=_ROUNDUP_CAPTION, created_by=_actor(s))
        _audit(tx, s, 'growth.queue.roundup', request_key=rk)
    return dict(request_key=rk)


@post('/admin/growth/queue/expire-approvals')
def post_growth_queue_expire_approvals():
    s = _gate()
    with api_tx() as tx:
        n = expire_member_approvals(tx)
        _audit(tx, s, 'growth.queue.expire_approvals', cancelled=n)
    return dict(cancelled=n)


@get('/admin/growth/settings')
def get_growth_settings():
    _gate()
    with api_tx('read committed') as tx:
        return settings(tx)


@get('/admin/growth/removals')
def get_growth_removals():
    _gate()
    pending = request.args.get('pending') in ('1', 'true', 'yes')
    with api_tx('read committed') as tx:
        rows = tx.execute(_Q_REMOVALS, dict(pending=pending)).fetchall()
    return jsonify([_row(r) for r in rows])


@post('/admin/growth/removals/<int:removal_id>/done')
def post_growth_removal_done(removal_id: int):
    s = _gate()
    with api_tx() as tx:
        updated = tx.execute(
            "UPDATE spotlight_removal_task SET done_at = NOW() WHERE id = %(i)s AND done_at IS NULL",
            dict(i=removal_id)).rowcount
        _audit(tx, s, 'growth.removal.done', removal_id=removal_id)
    return dict(ok=True, updated=updated)


@post('/admin/growth/token-health')
def post_growth_token_health():
    s = _gate()
    body = _body()
    expires_at = body.get('expires_at')
    valid = bool(body.get('valid'))
    with api_tx() as tx:
        set_setting(tx, 'token_expires_at', expires_at or '')
        set_setting(tx, 'token_valid', 'true' if valid else 'false')
        _audit(tx, s, 'growth.token.health', valid=valid, expires_at=expires_at)
    return dict(ok=True)


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
        cur = tx.execute(
            """UPDATE publishing_queue SET caption = %(c)s, updated_at = NOW()
                WHERE id = %(id)s AND status NOT IN ('published', 'cancelled')""",
            dict(c=caption, id=queue_id))
        if not cur.rowcount:
            abort(409)
        _audit(tx, s, 'growth.queue.caption', queue_id=str(queue_id))
    return dict(ok=True, caption=caption)


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
