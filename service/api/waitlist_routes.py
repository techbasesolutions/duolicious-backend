"""Public POST /waitlist - pre-signup waitlist capture.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import block. Unauthenticated (these users have no
account yet); rate-limited by IP via the shared limiter. Email is validated +
normalized by the PostWaitlist model (EmailStr); answers is an
onboarding-shaped JSONB blob persisted as-is for a magic-link launch flow.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import post, validate, shared_otp_limit
from database import api_tx
from service.waitlist import upsert


@post('/waitlist', limiter=shared_otp_limit)
@validate(t.PostWaitlist)
def post_waitlist(req: t.PostWaitlist):
    answers = req.answers if isinstance(req.answers, dict) else {}
    with api_tx() as tx:
        upsert(tx, req.email, answers)
    return {'ok': True}
