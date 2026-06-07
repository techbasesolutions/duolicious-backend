"""Public POST /account-check — existence probe for the sign-up email step.

Sibling module (imported at the bottom of service/api/__init__.py).
The web's /auth/sign-up calls this BEFORE /request-otp so a returning
user with an existing person row can be short-circuited into the
/auth/sign-in flow instead of wasting an OTP + walking through the
sign-up form.

Read-only. Public (no auth). Rate-limited by IP. Returns:

    { exists: bool }

where `exists` is True iff a person row exists for this normalized
email. We intentionally do NOT report onboardee-only rows here — a
mid-onboarding user can still complete sign-up via the existing
verify-email handoff once their OTP succeeds.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import post, validate, limiter, _is_private_ip
from database import api_tx


# Same shape + cap as the existing waitlist_check_limit — read-only,
# cheap probe; limit blunts email enumeration without throttling real
# users (the sign-up form fires it at most once per submit).
_account_check_limit = limiter.shared_limit(
    "20 per minute",
    scope="account_check",
    exempt_when=_is_private_ip,
)


_Q_PERSON_EXISTS = """
    SELECT 1
      FROM person
     WHERE normalized_email = %(email)s
        OR email = %(email)s
     LIMIT 1
"""


@post('/account-check', limiter=_account_check_limit)
@validate(t.PostAccountCheck)
def post_account_check(req: t.PostAccountCheck):
    with api_tx() as tx:
        row = tx.execute(_Q_PERSON_EXISTS, dict(email=req.email)).fetchone()
    return {'exists': row is not None}
