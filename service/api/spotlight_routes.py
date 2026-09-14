"""Public GET/POST /spotlight/confirm/<token>: signed opt-in link from the
Community Spotlight invite email (E1, spec 3.1).

Mounted unauthenticated, mirroring service/api/unsubscribe_routes.py: the
token is HMAC-signed (service.spotlight) and encodes the recipient email,
a minted-at timestamp (so the link expires after 30 days) and a single-use
nonce bound to the member's consent epoch (Wave 1 F11). GET only decodes
the token and reports current state so the confirmation page can render
the masked email and already-opted-in copy before the member commits to
anything: it must never write, not even to the nonce's `used_at`. POST is
the only endpoint that sets spotlight_opt_in, and consumes the nonce on
its first successful use so a stolen or leaked link cannot be replayed
after the member withdraws (a withdrawal bumps the epoch and burns any
still-unused nonce -- see service.spotlight.withdrawal.withdraw_member).
"""
from __future__ import annotations

from flask import abort, jsonify

from database import api_tx
from emails.base import mask_email
from service.api.decorators import get, post, limiter, _is_private_ip
from service.spotlight import parse_confirm_token, set_spotlight_opt_in
from service.spotlight.nonce import check_nonce, consume_nonce


# Mirrors unsubscribe_routes.py's unsub_limit: most callers are real
# recipients clicking a single email link once.
spotlight_limit = limiter.shared_limit(
    "20 per minute",
    scope="spotlight",
    exempt_when=_is_private_ip,
)


def _resolve(token: str) -> tuple[str, str]:
    email, nonce, status = parse_confirm_token(token)
    if status == 'expired':
        abort(410)
    if status != 'ok' or not email or not nonce:
        abort(400)
    return email, nonce


@get('/spotlight/confirm/<token>', limiter=spotlight_limit)
def get_spotlight_confirm(token: str):
    email, nonce = _resolve(token)
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT id, spotlight_opt_in FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email)).fetchone()
        if not row:
            abort(400)
        state = check_nonce(tx, nonce, row['id'], 'confirm')
    return jsonify(email_masked=mask_email(email), already=bool(row['spotlight_opt_in']),
                   stale=state in ('stale', 'invalid'))


@post('/spotlight/confirm/<token>', limiter=spotlight_limit)
def post_spotlight_confirm(token: str):
    email, nonce = _resolve(token)
    with api_tx() as tx:
        row = tx.execute("SELECT id, spotlight_opt_in FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email)).fetchone()
        if not row:
            abort(400)
        state = check_nonce(tx, nonce, row['id'], 'confirm')
        if state == 'ok':
            consume_nonce(tx, nonce)
            set_spotlight_opt_in(tx, row['id'], True)
            return jsonify(ok=True, already=False)
        if state == 'used':
            # A used nonce with the member now opted out means a withdrawal
            # happened through a path that did not bump the epoch -- fail
            # closed rather than silently re-confirming.
            if row['spotlight_opt_in']:
                return jsonify(ok=True, already=True)
            return jsonify(error='stale'), 410
        if state == 'stale':
            return jsonify(error='stale'), 410
        abort(400)  # invalid
