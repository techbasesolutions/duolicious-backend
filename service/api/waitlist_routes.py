"""Public POST /waitlist - pre-signup waitlist capture.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import block. Unauthenticated (these users have no
account yet); rate-limited by IP via the shared limiter. Email is validated +
normalized by the PostWaitlist model (EmailStr); answers is an
onboarding-shaped JSONB blob persisted as-is for a magic-link launch flow.
"""

from __future__ import annotations

import duotypes as t
from flask import request
from service.api.decorators import get, post, validate, shared_otp_limit, shared_recipient_limit, limiter, _is_private_ip
from database import api_tx
from service.waitlist import upsert, count as waitlist_count, get as waitlist_get
from service.beta import is_beta
from service.referrals import attribute as attribute_referral
from service.antibot import is_honeypot_hit, verify_turnstile
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


@post('/waitlist', limiter=[shared_otp_limit, shared_recipient_limit])
@validate(t.PostWaitlist)
def post_waitlist(req: t.PostWaitlist):
    # Honeypot — silently pretend success so bots can't detect rejection.
    if is_honeypot_hit(req.website):
        return {'ok': True, 'isNew': False}
    # Turnstile — no-op when TURNSTILE_SECRET_KEY unset.
    if not verify_turnstile(req.turnstile_token, request.remote_addr):
        return 'Verification failed', 403
    answers = req.answers if isinstance(req.answers, dict) else {}
    with api_tx() as tx:
        res = upsert(tx, req.email, answers)
        total = waitlist_count(tx)
        beta_flag = is_beta(tx, req.email)
        # Record referral attribution if the FE carried an inviter_code
        # from /i/<code>. Pre-launch most invitees land here (waitlist
        # is the default destination). Best-effort — bad codes / self-
        # referrals / already-attributed invitees return None silently.
        if res["inserted"]:
            attribute_referral(tx, req.inviter_code, req.email)
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
