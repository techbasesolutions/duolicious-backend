"""Authenticated POST /beta-tester - onboarding beta opt-in.

Sibling module (imported at the bottom of service/api/__init__.py). The user is
signed in during onboarding; identity (email + person_id) comes from the
session via @apost, never the request body, so it cannot be spoofed. Records the
beta_signup row and, on a brand-new row, fires the confirmation email.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import apost, limiter, _is_private_ip
from database import api_tx
from service.beta import register as register_beta
from emails.beta_welcome import send_beta_welcome_async

beta_limit = limiter.shared_limit(
    "10 per minute",
    scope="beta",
    exempt_when=_is_private_ip,
)


# expected_onboarding_status=None / expected_sign_in_status=None → any valid
# session is accepted (the opt-in fires right as the onboardee graduates into a
# person, so we don't pin a specific onboarding/sign-in state).
@apost(
    '/beta-tester',
    limiter=beta_limit,
    expected_onboarding_status=None,
    expected_sign_in_status=None,
)
def post_beta_tester(s: t.SessionInfo):
    with api_tx() as tx:
        is_new = register_beta(tx, s.email, s.person_id)
    if is_new:
        send_beta_welcome_async(s.email)
    return {'ok': True}
