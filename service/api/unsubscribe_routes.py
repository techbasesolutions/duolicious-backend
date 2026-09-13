"""Public GET/POST /u/<token> — one-click unsubscribe for waitlist + beta mail.

Mounted unauthenticated. The token is HMAC-signed (service.unsubscribe) and
encodes (scope, email) so a click stamps unsubscribed_at (or the matching
column) on the right row without the recipient needing to log in.
Idempotent: re-visiting the link after the first unsubscribe shows the same
success page.

GET only renders a confirmation form; it never stamps. Mail scanners
(Outlook Safe Links, corporate link-proxies, preview bots) follow every GET
link in an email automatically, so a GET that wrote to the database would
silently unsubscribe people who never clicked anything. Only a human
submitting that form, or the RFC 8058 one-click POST that Gmail/Yahoo issue
on the user's behalf, stamps the row -- both are POST. Pattern mirrors
service.person._confirm_form_html (GET serves a form, POST performs the
action).
"""

from __future__ import annotations

from html import escape as html_escape

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


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex, nofollow" />
<title>{title} · Ahavah</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #ECE9E0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; color: #0F0B1F; }}
  main {{ max-width: 480px; margin: 80px auto; padding: 36px 28px; background: #FBF9F4; border-radius: 18px; box-shadow: 0 20px 60px rgba(15,11,31,0.06); }}
  h1 {{ margin: 0 0 12px; font-size: 22px; font-weight: 800; letter-spacing: -0.01em; }}
  p {{ margin: 0 0 16px; line-height: 1.55; color: #565273; font-size: 16px; }}
  a {{ color: #5524F5; font-weight: 600; text-decoration: none; }}
  button {{ display: block; width: 100%; margin: 4px 0 0; padding: 16px; border: 0; border-radius: 12px; background: #0F0B1F; color: #ffffff; font-family: inherit; font-weight: 700; font-size: 16px; cursor: pointer; }}
  button:hover {{ background: #1A1340; }}
</style>
</head>
<body>
  <main>
    {body}
  </main>
</body>
</html>"""


def _invalid_token_html() -> str:
    return _page("Unsubscribe", f"""
    <h1>Unsubscribe</h1>
    <p>This unsubscribe link is invalid or has been tampered with. If you
    want to unsubscribe, reply to any Ahavah email and we will take you off
    the list manually.</p>
    """)


def _pending_html(token: str) -> str:
    # Nothing has happened yet: this is the GET response, so it only renders
    # a form. Submitting it (POST) is the action that stamps the row.
    safe_token = html_escape(token)
    return _page("Unsubscribe", f"""
    <h1>Unsubscribe from Ahavah mail</h1>
    <p>Nothing has happened yet. Click the button below to stop these
    emails.</p>
    <form method="post" action="/u/{safe_token}">
      <button type="submit">Unsubscribe</button>
    </form>
    """)


def _success_html(scope: str) -> str:
    return _page("Unsubscribed", f"""
    <h1>Unsubscribed</h1>
    <p>You have been removed from Ahavah's <strong>{scope}</strong> mail.
    We will not send you any more. Final transactional messages (like a
    deletion confirmation) may still arrive.</p>
    <p>Changed your mind? Just re-join from <a href="https://ahavah.app">ahavah.app</a>.</p>
    """)


@get('/u/<token>', limiter=unsub_limit)
def get_unsubscribe(token: str):
    from flask import Response
    parsed = parse_token(token)
    if not parsed:
        return Response(_invalid_token_html(), status=400,
                        headers={"Content-Type": "text/html; charset=utf-8"})
    return Response(_pending_html(token), status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"})


@post('/u/<token>', limiter=unsub_limit)
def post_unsubscribe(token: str):
    # Stamps the row. Reached either by a human submitting the form the GET
    # rendered, or by the RFC 8058 one-click POST that Gmail/Yahoo's
    # bulk-sender path issues automatically when the user hits the
    # inbox-level Unsubscribe button.
    from flask import Response
    parsed = parse_token(token)
    if not parsed:
        return Response(_invalid_token_html(), status=400,
                        headers={"Content-Type": "text/html; charset=utf-8"})

    scope, email = parsed
    with api_tx() as tx:
        # Recipient never had a row (or was deleted): treat as success so we
        # don't leak whether the email is in our DB. Same HTML either way.
        stamp_unsubscribed(tx, scope, email)

    return Response(_success_html(scope), status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"})
