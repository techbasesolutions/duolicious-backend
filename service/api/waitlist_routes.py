"""Public POST /waitlist - pre-signup waitlist capture.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import block. Unauthenticated (these users have no
account yet); rate-limited by IP via the shared limiter. Email is validated +
normalized by the PostWaitlist model (EmailStr); answers is an
onboarding-shaped JSONB blob persisted as-is for a magic-link launch flow.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import get, post, validate, shared_otp_limit
from database import api_tx
from service.waitlist import upsert, count as waitlist_count
from emails.waitlist_welcome import send_waitlist_welcome_async


@post('/waitlist', limiter=shared_otp_limit)
@validate(t.PostWaitlist)
def post_waitlist(req: t.PostWaitlist):
    answers = req.answers if isinstance(req.answers, dict) else {}
    with api_tx() as tx:
        is_new = upsert(tx, req.email, answers)
    # Welcome email fires once, only on a brand-new signup (the landing posts
    # {email} first, then the wizard re-upserts answers — we don't resend).
    # Fire-and-forget so the response isn't blocked on SMTP.
    if is_new:
        send_waitlist_welcome_async(req.email)
    # isNew=false → returning registrant (the web shows a "Welcome back" variant).
    return {'ok': True, 'isNew': is_new}


@get('/waitlist/count')
def get_waitlist_count():
    # Public social-proof count. Flask serializes the dict to JSON.
    with api_tx() as tx:
        n = waitlist_count(tx)
    return {'count': n}
