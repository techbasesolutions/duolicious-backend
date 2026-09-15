"""Signed Spotlight card-approval tokens and card state (spec 3.1, 3.4, 3.5,
section 2).

Mirrors `service.spotlight`'s HMAC token scheme (same shared secret,
`_now_ts`, base64url without padding), but a card token binds a
`request_key` together with the recipient email so the link can only
decide the one request it was minted for, and only from the mailbox it
was sent to. On top of the signature, a token carries a single-use nonce
(Wave 1 F11, `service.spotlight.nonce`) minted against the recipient's
current `spotlight_consent_epoch`: replaying a card link after the member
withdraws reads back 'stale' even though the signature and TTL still
check out. GET never mutates: `card_state` only reads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote

import service.spotlight.storage as st
from service.config import WEB_BASE_URL
from service.spotlight.eligibility import photo_url
from service.spotlight.nonce import issue_nonce
from service.spotlight.revisions import current_revision
from service.unsubscribe import _secret  # same key, fails closed when unset

CARD_TOKEN_TTL_SECONDS = 7 * 24 * 3600


def _now_ts() -> int:
    return int(time.time())


def _sign(request_key: str, email: str, ts: int, nonce: str) -> str:
    payload = f"card|{request_key}|{email}|{ts}|{nonce}".encode()
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip('=')


def make_card_token(tx, request_key: str, email: str) -> str | None:
    """None when no activated person has this email -- there is nothing to
    mint a single-use nonce against."""
    norm = email.strip().lower()
    row = tx.execute("SELECT id FROM person WHERE lower(email) = %(e)s AND activated", dict(e=norm)).fetchone()
    if not row:
        return None
    nonce = issue_nonce(tx, row['id'], 'card')
    ts = _now_ts()
    e = base64.urlsafe_b64encode(norm.encode()).decode().rstrip('=')
    return f"{request_key}.{e}.{ts}.{nonce}.{_sign(request_key, norm, ts, nonce)}"


def parse_card_token(token: str) -> tuple[str | None, str | None, str | None, str]:
    """Returns (request_key, email, nonce, status) where status is 'ok',
    'invalid' or 'expired'. Pure: signature and TTL only. An old 4-segment
    token (minted before this nonce segment existed) is rejected as
    'invalid' -- nothing was sent under the old format, so there is
    nothing to honour. Nonce state (used/stale) is checked separately,
    inside a transaction, via `service.spotlight.nonce.check_nonce`."""
    parts = (token or '').split('.')
    if len(parts) != 5:
        return None, None, None, 'invalid'
    rk, e, ts_s, nonce, sig = parts
    try:
        email = base64.urlsafe_b64decode(e + '=' * (-len(e) % 4)).decode()
        ts = int(ts_s)
    except Exception:
        return None, None, None, 'invalid'
    if not nonce or not hmac.compare_digest(_sign(rk, email, ts, nonce), sig):
        return None, None, None, 'invalid'
    if _now_ts() - ts > CARD_TOKEN_TTL_SECONDS:
        return None, None, None, 'expired'
    return rk, email, nonce, 'ok'


def card_url(tx, request_key: str, email: str) -> str | None:
    token = make_card_token(tx, request_key, email)
    if token is None:
        return None
    return f"{WEB_BASE_URL.rstrip('/')}/spotlight/card/{quote(token)}"


# One row per request_key (both platform rows carry the same subject,
# kind and queue status), so the facebook row (or the first platform
# alphabetically when facebook has no row) stands in for the pair. Content
# (caption, photo, render) is no longer read off the queue row at all --
# it comes from the current revision, so a card in flight always reflects
# exactly what the member is being asked to consent to.
_Q_STATE = """
    SELECT q.subject_person_id, q.kind, q.status,
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
    rev = current_revision(tx, request_key)
    # Consent never carries over between revisions (spec: the primary key is
    # per revision), so "approved" only ever reflects the CURRENT revision --
    # an older revision's consent row, if any, is irrelevant here.
    consented = False
    if rev and row['subject_person_id'] is not None:
        consented = tx.execute(
            """SELECT 1 FROM spotlight_revision_consent
                WHERE revision_id = %(rid)s AND person_id = %(pid)s AND role = 'subject'""",
            dict(rid=rev['id'], pid=row['subject_person_id'])).fetchone() is not None
    return dict(
        subject_person_id=row['subject_person_id'],
        email=row['email'],
        first_name=row['first_name'],
        age=row['age'],
        country=row['country'],
        status=row['status'],
        photos=[dict(uuid=r['uuid'], url=photo_url(r['uuid'])) for r in photo_rows],
        kind=row['kind'],
        caption=rev['caption'] if rev else None,
        photo_uuid=rev['photo_uuid'] if rev else None,
        revision=rev['revision'] if rev else None,
        preview_available=bool(rev and rev['asset_hash']),
        # The object is private until the card is scheduled (Wave 2 F09), so
        # the member's own preview link is a short-lived presigned read, not
        # the (not yet public) stored URL -- minted fresh on every GET rather
        # than cached, since a 15-minute link handed out on an earlier read
        # could already be expired by the time this one is served.
        image_url=(st.presign(rev['image_key']) if rev and rev['image_key'] else None),
        consented=consented,
    )
