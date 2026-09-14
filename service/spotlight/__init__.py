"""Spotlight consent (spec 3.1).

Community Spotlight (Phase B) features an opted-in member's profile in a
periodic email. Consent is a dedicated `person.spotlight_opt_in` flag
(migration 0039) rather than folding onto an existing preference, since
it is a distinct outward-facing feature the member must explicitly
choose (not a mail-preference toggle).

Members opt in two ways:
  - `PATCH /profile-info {"spotlight_opt_in": true|false}` while signed in
    (see `service.person.patch_profile_info`).
  - Clicking the signed link in the invite email (E1), which hits the
    unauthenticated `/spotlight/confirm/<token>` routes below.

Tokens reuse the unsubscribe HMAC key (`service.unsubscribe._secret`) so
there is one signing secret to rotate, but mint their own format with a
`ts` segment so links expire after 30 days, unlike unsubscribe links,
which must work indefinitely. On top of the signature, a token carries a
single-use nonce (Wave 1 F11, `service.spotlight.nonce`) minted against
the recipient's current `spotlight_consent_epoch`: replaying a confirm
link after the member withdraws reads back 'stale' even though the
signature and TTL still check out, since `withdraw_member` bumps the
epoch and burns any unused nonce. GET never mutates: it only decodes the
token and reports the member's current state so the confirmation page
can render before the member commits to anything.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import quote

from service.config import WEB_BASE_URL
from service.spotlight.nonce import issue_nonce
from service.unsubscribe import _secret  # same key, fails closed when unset

TOKEN_TTL_SECONDS = 30 * 24 * 3600


def _now_ts() -> int:
    return int(time.time())


def _sign(email: str, ts: int, nonce: str) -> str:
    payload = f"spotlight|{email}|{ts}|{nonce}".encode()
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip('=')


def make_confirm_token(tx, email: str) -> str | None:
    """None when no activated person has this email -- there is nothing to
    mint a single-use nonce against."""
    norm = email.strip().lower()
    row = tx.execute("SELECT id FROM person WHERE lower(email) = %(e)s AND activated", dict(e=norm)).fetchone()
    if not row:
        return None
    nonce = issue_nonce(tx, row['id'], 'confirm')
    ts = _now_ts()
    e = base64.urlsafe_b64encode(norm.encode()).decode().rstrip('=')
    return f"{e}.{ts}.{nonce}.{_sign(norm, ts, nonce)}"


def parse_confirm_token(token: str) -> tuple[str | None, str | None, str]:
    """Returns (email, nonce, status) where status is 'ok', 'invalid' or
    'expired'. Pure: signature and TTL only. An old 3-segment token (minted
    before this nonce segment existed) is rejected as 'invalid' -- nothing
    was sent under the old format, so there is nothing to honour. Nonce
    state (used/stale) is checked separately, inside a transaction, via
    `service.spotlight.nonce.check_nonce`."""
    parts = (token or '').split('.')
    if len(parts) != 4:
        return None, None, 'invalid'
    e, ts_s, nonce, sig = parts
    try:
        email = base64.urlsafe_b64decode(e + '=' * (-len(e) % 4)).decode()
        ts = int(ts_s)
    except Exception:
        return None, None, 'invalid'
    if not nonce or not hmac.compare_digest(_sign(email, ts, nonce), sig):
        return None, None, 'invalid'
    if _now_ts() - ts > TOKEN_TTL_SECONDS:
        return None, None, 'expired'
    return email, nonce, 'ok'


def spotlight_confirm_url(tx, email: str) -> str | None:
    token = make_confirm_token(tx, email)
    if token is None:
        return None
    return f"{WEB_BASE_URL.rstrip('/')}/spotlight/confirm/{quote(token)}"


def set_spotlight_opt_in(tx, person_id: int, value: bool) -> None:
    tx.execute(
        """
        UPDATE person
           SET spotlight_opt_in = %(v)s,
               spotlight_opt_in_at = CASE WHEN %(v)s THEN NOW() ELSE spotlight_opt_in_at END
         WHERE id = %(id)s
        """,
        dict(v=value, id=person_id))
    if not value:
        from service.spotlight.withdrawal import withdraw_member
        withdraw_member(tx, person_id, 'opt_out')
