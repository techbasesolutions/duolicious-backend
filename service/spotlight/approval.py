"""Signed Spotlight card-approval tokens and card state (spec 3.1, 3.4, 3.5,
section 2).

Mirrors `service.spotlight`'s HMAC token scheme (same shared secret,
`_now_ts`, base64url without padding), but a card token binds a
`request_key` together with the recipient email so the link can only
decide the one request it was minted for, and only from the mailbox it
was sent to. GET never mutates: `card_state` only reads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote

from service.config import WEB_BASE_URL
from service.spotlight.eligibility import photo_url
from service.unsubscribe import _secret  # same key, fails closed when unset

CARD_TOKEN_TTL_SECONDS = 7 * 24 * 3600


def _now_ts() -> int:
    return int(time.time())


def _sign(request_key: str, email: str, ts: int) -> str:
    payload = f"card|{request_key}|{email}|{ts}".encode()
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip('=')


def make_card_token(request_key: str, email: str) -> str:
    norm = email.strip().lower()
    ts = _now_ts()
    e = base64.urlsafe_b64encode(norm.encode()).decode().rstrip('=')
    return f"{request_key}.{e}.{ts}.{_sign(request_key, norm, ts)}"


def parse_card_token(token: str) -> tuple[str | None, str | None, str]:
    """Returns (request_key, email, status) where status is 'ok', 'invalid'
    or 'expired'."""
    parts = (token or '').split('.')
    if len(parts) != 4:
        return None, None, 'invalid'
    rk, e, ts_s, sig = parts
    try:
        email = base64.urlsafe_b64decode(e + '=' * (-len(e) % 4)).decode()
        ts = int(ts_s)
    except Exception:
        return None, None, 'invalid'
    if not hmac.compare_digest(_sign(rk, email, ts), sig):
        return None, None, 'invalid'
    if _now_ts() - ts > CARD_TOKEN_TTL_SECONDS:
        return None, None, 'expired'
    return rk, email, 'ok'


def card_url(request_key: str, email: str) -> str:
    return f"{WEB_BASE_URL.rstrip('/')}/spotlight/card/{quote(make_card_token(request_key, email))}"


# One row per request_key (both platform rows carry the same subject,
# kind, caption and approval state), so the facebook row (or the first
# platform alphabetically when facebook has no row) stands in for the pair.
_Q_STATE = """
    SELECT q.subject_person_id, q.kind, q.caption, q.image_url, q.status,
           q.approved_photo_uuid::text AS approved_photo_uuid, q.member_approved_at,
           p.email, split_part(p.name, ' ', 1) AS first_name,
           date_part('year', age(p.date_of_birth))::int AS age,
           COALESCE(p.country, p.location_short_friendly) AS country
      FROM publishing_queue q
      LEFT JOIN person p ON p.id = q.subject_person_id
     WHERE q.request_key = %(rk)s
     ORDER BY q.platform
     LIMIT 1
"""

_Q_PHOTOS = """
    SELECT uuid::text AS uuid
      FROM photo
     WHERE person_id = %(pid)s AND moderation_status = 'approved'
     ORDER BY position
"""


def card_state(tx, request_key: str) -> dict | None:
    row = tx.execute(_Q_STATE, dict(rk=request_key)).fetchone()
    if not row:
        return None
    photo_rows = tx.execute(_Q_PHOTOS, dict(pid=row['subject_person_id'])).fetchall()
    return dict(
        subject_person_id=row['subject_person_id'],
        email=row['email'],
        first_name=row['first_name'],
        age=row['age'],
        country=row['country'],
        status=row['status'],
        photos=[dict(uuid=r['uuid'], url=photo_url(r['uuid'])) for r in photo_rows],
        approved_photo_uuid=row['approved_photo_uuid'],
        member_approved_at=row['member_approved_at'],
        kind=row['kind'],
        caption=row['caption'],
        image_url=row['image_url'],
    )
