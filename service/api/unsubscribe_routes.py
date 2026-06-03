"""Public GET/POST /u/<token> — one-click unsubscribe for waitlist + beta mail.

Mounted unauthenticated. The token is HMAC-signed (service.unsubscribe) and
encodes (scope, email) so a click stamps unsubscribed_at on the right row
without the recipient needing to log in. Idempotent: re-visiting the link
after the first unsubscribe shows the same confirmation.

GET serves an HTML confirmation (the user lands here by clicking the
footer link). POST is the RFC 8058 One-Click endpoint that Gmail / Yahoo
will hit automatically on the user's behalf; we accept it identically.
"""

from __future__ import annotations

from service.api.decorators import get, post, limiter, _is_private_ip
from database import api_tx
from service.unsubscribe import parse_token, stamp_unsubscribed


# Looser limit — most callers are real recipients clicking a single link;
# only Gmail's one-click POST might fire more than once per email.
unsub_limit = limiter.shared_limit(
    "20 per minute",
    scope="unsubscribe",
    exempt_when=_is_private_ip,
)


def _confirmation_html(message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex, nofollow" />
<title>Unsubscribed · Ahavah</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #ECE9E0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; color: #0F0B1F; }}
  main {{ max-width: 480px; margin: 80px auto; padding: 36px 28px; background: #FBF9F4; border-radius: 18px; box-shadow: 0 20px 60px rgba(15,11,31,0.06); }}
  h1 {{ margin: 0 0 12px; font-size: 22px; font-weight: 800; letter-spacing: -0.01em; }}
  p {{ margin: 0 0 16px; line-height: 1.55; color: #565273; font-size: 16px; }}
  a {{ color: #5524F5; font-weight: 600; text-decoration: none; }}
</style>
</head>
<body>
  <main>
    <h1>Unsubscribed</h1>
    <p>{message}</p>
    <p>Changed your mind? Just re-join from <a href="https://ahavah.app">ahavah.app</a>.</p>
  </main>
</body>
</html>"""


def _do_unsubscribe(token: str) -> tuple[str, int, dict]:
    parsed = parse_token(token)
    if not parsed:
        return (_confirmation_html(
            "This unsubscribe link is invalid or has been tampered with. "
            "If you want to unsubscribe, reply to any Ahavah email and we'll "
            "take you off the list manually."
        ), 400, {"Content-Type": "text/html; charset=utf-8"})

    scope, email = parsed
    with api_tx() as tx:
        ok = stamp_unsubscribed(tx, scope, email)

    if not ok:
        # Recipient never had a row (or was deleted) — treat as success so
        # we don't leak whether the email is in our DB. Same HTML.
        pass
    return (_confirmation_html(
        f"You've been removed from Ahavah's <strong>{scope}</strong> mail. "
        f"We won't send you any more. Final transactional messages (like a "
        f"deletion confirmation) may still arrive."
    ), 200, {"Content-Type": "text/html; charset=utf-8"})


@get('/u/<token>', limiter=unsub_limit)
def get_unsubscribe(token: str):
    body, status, headers = _do_unsubscribe(token)
    from flask import Response
    return Response(body, status=status, headers=headers)


@post('/u/<token>', limiter=unsub_limit)
def post_unsubscribe(token: str):
    # RFC 8058 one-click POST. Gmail/Yahoo's bulk-sender path issues this
    # automatically when the user hits the inbox-level Unsubscribe button.
    body, status, headers = _do_unsubscribe(token)
    from flask import Response
    return Response(body, status=status, headers=headers)
