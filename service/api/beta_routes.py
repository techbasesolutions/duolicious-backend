"""Public POST /beta-tester - beta opt-in from the waitlist completion screen.

Sibling module (imported at the bottom of service/api/__init__.py). The opt-in
lives in the PUBLIC waitlist flow (no account yet), so identity is the email the
user just entered, validated by PostBetaTester. Rate-limited like the other
public lead forms. Records the beta_signup row (person_id NULL) and, on a
brand-new row, fires the confirmation email promising a June 15 sign-in link.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import post, validate, limiter, shared_recipient_limit, _is_private_ip
from database import api_tx
from service.beta import register as register_beta, count as beta_count
from emails.beta_welcome import send_beta_welcome_async
from emails.waitlist_admin import send_beta_optin_notice_async

beta_limit = limiter.shared_limit(
    "10 per minute",
    scope="beta",
    exempt_when=_is_private_ip,
)


@post('/beta-tester', limiter=[beta_limit, shared_recipient_limit])
@validate(t.PostBetaTester)
def post_beta_tester(req: t.PostBetaTester):
    with api_tx() as tx:
        is_new = register_beta(tx, req.email, None)
        total = beta_count(tx)
    if is_new:
        send_beta_welcome_async(req.email)
        # Notify the admin inbox of the new beta opt-in (mirrors the signup notice).
        send_beta_optin_notice_async(req.email, total)
    # isNew=false → already a beta tester (the card shows an "already in" state).
    return {'ok': True, 'isNew': is_new}
