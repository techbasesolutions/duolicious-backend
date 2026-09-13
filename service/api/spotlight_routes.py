"""Public GET/POST /spotlight/confirm/<token> — signed opt-in link from the
Community Spotlight invite email (E1, spec 3.1).

Mounted unauthenticated, mirroring service/api/unsubscribe_routes.py: the
token is HMAC-signed (service.spotlight) and encodes the recipient email
plus a minted-at timestamp so the link expires after 30 days. GET only
decodes the token and reports current state so the confirmation page can
render the masked email and already-opted-in copy before the member
commits to anything — it must never write. POST is the only endpoint that
sets spotlight_opt_in, and is idempotent so a repeat click (or a page
refresh after success) is harmless.
"""
from __future__ import annotations

from flask import abort, jsonify

from database import api_tx
from emails.base import mask_email
from service.api.decorators import get, post, limiter, _is_private_ip
from service.spotlight import parse_confirm_token, set_spotlight_opt_in_by_email


# Mirrors unsubscribe_routes.py's unsub_limit: most callers are real
# recipients clicking a single email link once.
spotlight_limit = limiter.shared_limit(
    "20 per minute",
    scope="spotlight",
    exempt_when=_is_private_ip,
)


def _resolve(token: str) -> str:
    email, status = parse_confirm_token(token)
    if status == 'expired':
        abort(410)
    if status != 'ok' or not email:
        abort(400)
    return email


@get('/spotlight/confirm/<token>', limiter=spotlight_limit)
def get_spotlight_confirm(token: str):
    email = _resolve(token)
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT spotlight_opt_in FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email)).fetchone()
    if not row:
        abort(400)
    return jsonify(email_masked=mask_email(email), already=bool(row['spotlight_opt_in']))


@post('/spotlight/confirm/<token>', limiter=spotlight_limit)
def post_spotlight_confirm(token: str):
    email = _resolve(token)
    with api_tx() as tx:
        if not set_spotlight_opt_in_by_email(tx, email, True):
            abort(400)
    return jsonify(ok=True)
