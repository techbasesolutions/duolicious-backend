"""Public POST /beta-tester - beta opt-in from the waitlist completion screen.

Sibling module (imported at the bottom of service/api/__init__.py). The opt-in
lives in the PUBLIC waitlist flow (no account yet), so identity is the email the
user just entered, validated by PostBetaTester. Rate-limited like the other
public lead forms. Records the beta_signup row (person_id NULL) and, on a
brand-new row, fires the confirmation email promising a June 15 sign-in link.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import post, validate, limiter, _is_private_ip
from database import api_tx
from service.beta import register as register_beta
from emails.beta_welcome import send_beta_welcome_async

beta_limit = limiter.shared_limit(
    "10 per minute",
    scope="beta",
    exempt_when=_is_private_ip,
)


@post('/beta-tester', limiter=beta_limit)
@validate(t.PostBetaTester)
def post_beta_tester(req: t.PostBetaTester):
    with api_tx() as tx:
        is_new = register_beta(tx, req.email, None)
    if is_new:
        send_beta_welcome_async(req.email)
    return {'ok': True}
