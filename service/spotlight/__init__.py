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
`ts` segment so links expire after 30 days — unlike unsubscribe links,
which must work indefinitely. GET never mutates: it only decodes the
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
from service.unsubscribe import _secret  # same key, fails closed when unset

TOKEN_TTL_SECONDS = 30 * 24 * 3600


def _now_ts() -> int:
    return int(time.time())


def _sign(email: str, ts: int) -> str:
    payload = f"spotlight|{email}|{ts}".encode()
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip('=')


def make_confirm_token(email: str) -> str:
    norm = email.strip().lower()
    ts = _now_ts()
    e = base64.urlsafe_b64encode(norm.encode()).decode().rstrip('=')
    return f"{e}.{ts}.{_sign(norm, ts)}"


def parse_confirm_token(token: str) -> tuple[str | None, str]:
    """Returns (email, status) where status is 'ok', 'invalid' or 'expired'."""
    parts = (token or '').split('.')
    if len(parts) != 3:
        return None, 'invalid'
    e, ts_s, sig = parts
    try:
        email = base64.urlsafe_b64decode(e + '=' * (-len(e) % 4)).decode()
        ts = int(ts_s)
    except Exception:
        return None, 'invalid'
    if not hmac.compare_digest(_sign(email, ts), sig):
        return None, 'invalid'
    if _now_ts() - ts > TOKEN_TTL_SECONDS:
        return None, 'expired'
    return email, 'ok'


def spotlight_confirm_url(email: str) -> str:
    return f"{WEB_BASE_URL.rstrip('/')}/spotlight/confirm/{quote(make_confirm_token(email))}"


def set_spotlight_opt_in(tx, person_id: int, value: bool) -> None:
    tx.execute(
        """
        UPDATE person
           SET spotlight_opt_in = %(v)s,
               spotlight_opt_in_at = CASE WHEN %(v)s THEN NOW() ELSE spotlight_opt_in_at END
         WHERE id = %(id)s
        """,
        dict(v=value, id=person_id))
    # Phase B hook: cancel queued cards for this member on opt-out.


def set_spotlight_opt_in_by_email(tx, email: str, value: bool) -> bool:
    row = tx.execute("SELECT id FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email.lower())).fetchone()
    if not row:
        return False
    set_spotlight_opt_in(tx, row['id'], value)
    return True
