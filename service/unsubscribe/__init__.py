"""Token-backed unsubscribe (Gmail / Yahoo one-click compliance).

A signed token encodes the recipient email and a scope (`waitlist` or
`beta`). The /u/<token> route stamps `unsubscribed_at` on the matching
row. Tokens are HMAC-SHA256 over `<scope>|<lowercase-email>` with the
SESSION_TOKEN_SECRET key — same key the rest of the app uses for
session signing, so rotating the secret rotates outstanding unsubscribe
links too (acceptable — recipients can request a new one).

No expiry: an unsubscribe link sent today should still work years from
now. The token does NOT carry the user's session — it's a one-purpose
key only valid for stamping unsubscribed_at on the matching row.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from urllib.parse import quote


_SCOPES = ("waitlist", "beta", "notifications")


def _secret() -> bytes:
    """HMAC key. Falls back to a dev-only constant if SESSION_TOKEN_SECRET
    is unset (it should always be set in prod — compose.production.yml
    requires it via `${VAR:?...}`)."""
    return (os.environ.get("SESSION_TOKEN_SECRET") or "dev-only-unsub-secret").encode()


def _sign(scope: str, email: str) -> str:
    payload = f"{scope}|{email.strip().lower()}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_token(scope: str, email: str) -> str:
    """Build an opaque token suitable for embedding in an unsubscribe URL."""
    if scope not in _SCOPES:
        raise ValueError(f"unknown unsubscribe scope: {scope}")
    norm = email.strip().lower()
    email_b64 = base64.urlsafe_b64encode(norm.encode()).decode().rstrip("=")
    sig = _sign(scope, norm)
    return f"{scope}.{email_b64}.{sig}"


def parse_token(token: str) -> tuple[str, str] | None:
    """Returns (scope, email) iff the token validates; None otherwise."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    scope, email_b64, sig = parts
    if scope not in _SCOPES:
        return None
    try:
        padding = "=" * (-len(email_b64) % 4)
        email = base64.urlsafe_b64decode(email_b64 + padding).decode()
    except Exception:
        return None
    expected = _sign(scope, email)
    if not hmac.compare_digest(expected, sig):
        return None
    return (scope, email)


def make_url(scope: str, email: str, web_base: str) -> str:
    """Full https URL to /u/<token>, ready to embed in an email footer."""
    token = make_token(scope, email)
    return f"{web_base.rstrip('/')}/u/{quote(token)}"


# SQL — stamp unsubscribed_at on the right table. Caller owns the api_tx.

_Q_UNSUB = {
    "waitlist": """
        UPDATE waitlist_signup
           SET unsubscribed_at = COALESCE(unsubscribed_at, NOW())
         WHERE email = %(email)s
        RETURNING (unsubscribed_at = NOW()) AS just_now
    """,
    "beta": """
        UPDATE beta_signup
           SET unsubscribed_at = COALESCE(unsubscribed_at, NOW())
         WHERE email = %(email)s
        RETURNING (unsubscribed_at = NOW()) AS just_now
    """,
    # Notification emails (match/like/verification/message fallbacks): turn
    # off every email_* channel for the matching person. Keyed by person, so
    # resolve email -> person and upsert the preference row.
    "notifications": """
        INSERT INTO notification_preference (
            person_id, email_messages, email_matches, email_likes,
            email_verification, email_profile_views, updated_at)
        SELECT p.id, FALSE, FALSE, FALSE, FALSE, FALSE, NOW()
          FROM person p WHERE lower(p.email) = %(email)s
        ON CONFLICT (person_id) DO UPDATE
           SET email_messages = FALSE, email_matches = FALSE,
               email_likes = FALSE, email_verification = FALSE,
               email_profile_views = FALSE, updated_at = NOW()
        RETURNING person_id
    """,
}


def stamp_unsubscribed(tx, scope: str, email: str) -> bool:
    """Stamp unsubscribed_at on the matching row. Idempotent (re-runs no-op).
    Returns True iff a row matched (regardless of whether it was the first
    time it was stamped).
    """
    if scope not in _Q_UNSUB:
        return False
    row = tx.execute(_Q_UNSUB[scope], dict(email=email.strip().lower())).fetchone()
    return row is not None
