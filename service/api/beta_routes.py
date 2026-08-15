"""Public POST /beta-tester - beta opt-in from the waitlist completion screen.

Sibling module (imported at the bottom of service/api/__init__.py). The opt-in
lives in the PUBLIC waitlist flow (no account yet), so identity is the email the
user just entered, validated by PostBetaTester. Rate-limited like the other
public lead forms. Records the beta_signup row (person_id NULL) and, on a
brand-new row, fires the confirmation email promising a June 15 sign-in link.
"""

from __future__ import annotations

import duotypes as t
from flask import request
from service.api.decorators import post, validate, limiter, shared_recipient_limit, _is_private_ip
from database import api_tx
from service.beta import register as register_beta, count as beta_count
from service.referrals import attribute as attribute_referral
from service.antibot import is_honeypot_hit, verify_turnstile
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
    # Honeypot — silently pretend success.
    if is_honeypot_hit(req.website):
        return {'ok': True, 'isNew': False}
    # Turnstile — no-op when TURNSTILE_SECRET_KEY unset.
    if not verify_turnstile(req.turnstile_token, request.remote_addr):
        return 'Verification failed', 403
    with api_tx() as tx:
        is_new, resubscribed = register_beta(tx, req.email, None)
        total = beta_count(tx)
        # Record referral attribution if the FE carried an inviter_code
        # from /i/<code>. Best-effort — bad codes / self-referrals /
        # already-attributed invitees return None and we move on without
        # affecting the beta-tester flow. See parent spec.
        if is_new:
            attribute_referral(tx, req.inviter_code, req.email)
    # F21: a re-opt-in after unsubscribing is explicit consent again, so it
    # gets the welcome + admin notice exactly like a brand-new signup. An
    # unchanged, still-subscribed row (is_new=False, resubscribed=False)
    # sends nothing - it's a genuine no-op repeat click.
    if is_new or resubscribed:
        send_beta_welcome_async(req.email)
        # Notify the admin inbox of the new beta opt-in (mirrors the signup notice).
        send_beta_optin_notice_async(req.email, total)
    # isNew=false → already a beta tester (the card shows an "already in" state).
    return {'ok': True, 'isNew': is_new}
