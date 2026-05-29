"""Public POST /waitlist - pre-signup waitlist capture.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import block. Unauthenticated (these users have no
account yet); rate-limited by IP via the shared limiter. Email is validated +
normalized by the PostWaitlist model (EmailStr); answers is an
onboarding-shaped JSONB blob persisted as-is for a magic-link launch flow.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import get, post, validate, shared_otp_limit, limiter, _is_private_ip
from database import api_tx
from service.waitlist import upsert, count as waitlist_count, get as waitlist_get
from service.beta import is_beta
from emails.waitlist_welcome import send_waitlist_welcome_async
from emails.waitlist_admin import (
    send_new_signup_notice_async,
    send_onboarding_complete_notice_async,
)

# Read-only existence probe — fires on the onboarding email step. Looser limit
# than the OTP/signup limiter (it's a cheap read), but still bounded to blunt
# email-enumeration.
waitlist_check_limit = limiter.shared_limit(
    "20 per minute",
    scope="waitlist_check",
    exempt_when=_is_private_ip,
)


@post('/waitlist', limiter=shared_otp_limit)
@validate(t.PostWaitlist)
def post_waitlist(req: t.PostWaitlist):
    answers = req.answers if isinstance(req.answers, dict) else {}
    with api_tx() as tx:
        res = upsert(tx, req.email, answers)
        total = waitlist_count(tx)
        beta_flag = is_beta(tx, req.email)
    is_new = res["inserted"]
    # On a brand-new signup (the landing posts {email} first, then the wizard
    # re-upserts answers), fire fire-and-forget emails so the response isn't
    # blocked on SMTP: the welcome to the signer-upper + a new-signup notice
    # to the admin inbox. The admin report carries the beta-tester status.
    if is_new:
        send_waitlist_welcome_async(req.email)
        send_new_signup_notice_async(req.email, answers, total, beta_flag)
    # When the row gains answers for the first time (the wizard completion),
    # notify the admin inbox that someone completed onboarding.
    if res["became_complete"]:
        send_onboarding_complete_notice_async(req.email, answers, beta_flag)
    # isNew=false → returning registrant (the web shows a "Welcome back" variant).
    return {'ok': True, 'isNew': is_new}


@get('/waitlist/count')
def get_waitlist_count():
    # Public social-proof count. Flask serializes the dict to JSON.
    with api_tx() as tx:
        n = waitlist_count(tx)
    return {'count': n}


@post('/waitlist/check', limiter=waitlist_check_limit)
@validate(t.PostWaitlistCheck)
def post_waitlist_check(req: t.PostWaitlistCheck):
    # Read-only: does this email already exist, and is it a *completed* signup?
    # The landing hero posts {email} first (an empty-answers row), then redirects
    # into the wizard. If we short-circuited on mere existence, that early-capture
    # row would skip every demographic step. So `complete` is true only when the
    # row carries answers — the web short-circuits to "already in" only then,
    # otherwise it walks the wizard and upserts the answers over the empty row.
    with api_tx() as tx:
        row = waitlist_get(tx, req.email)
    answers = row.get('answers') if row else None
    complete = isinstance(answers, dict) and len(answers) > 0
    return {'exists': row is not None, 'complete': complete}
