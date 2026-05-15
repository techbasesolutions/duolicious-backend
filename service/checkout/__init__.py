"""
Phase W cutover — Stripe Checkout for web subscription purchases.

Mirrors the structure of service/identity_verification: lazy-init Stripe,
same 503-on-not-configured opt-in pattern. Two endpoints:

  POST /checkout/web {tier_key}
       → creates a Stripe Checkout session for `tier_key` ('month' /
         'quart' / 'year'), returns {"url": "https://checkout.stripe.com/…"}

  POST /webhooks/stripe-checkout
       → Stripe-signed webhook. On checkout.session.completed grants the
         'premium' entitlement (with expiry derived from the tier) and
         stamps person.stripe_customer_id. On customer.subscription.deleted
         (or .updated with status='canceled') revokes the entitlement.
         Idempotent via service.entitlements.record_event(event.id) — Stripe
         retries are swallowed without double-applying.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier → expiry helper
# ---------------------------------------------------------------------------
#
# Used to compute the entitlement expiry stamp on grant. The webhook also
# inspects the subscription's `current_period_end` if present (more
# accurate than fixed-interval math), and we take the max of the two so a
# Stripe extension can never demote the user before its real cutoff.

_TIER_EXPIRY_DAYS: dict[str, int] = {
    'month': 31,
    'quart': 31 * 3 + 1,
    'year':  366,
}


# ---------------------------------------------------------------------------
# Lazy Stripe configuration (same pattern as identity_verification)
# ---------------------------------------------------------------------------

_stripe_init_attempted = False
_stripe_module = None


def _stripe():
    """Returns the configured stripe module, or None if STRIPE_SECRET_KEY is unset."""
    global _stripe_init_attempted, _stripe_module
    if _stripe_init_attempted:
        return _stripe_module
    _stripe_init_attempted = True

    api_key = os.environ.get('STRIPE_SECRET_KEY')
    if not api_key:
        logger.info('STRIPE_SECRET_KEY not set; /checkout/web will 503')
        return None

    try:
        import stripe
        stripe.api_key = api_key
        _stripe_module = stripe
    except ImportError:
        logger.warning('stripe package not installed; /checkout/web will 503')
        _stripe_module = None
    return _stripe_module


# ---------------------------------------------------------------------------
# Tier → Stripe price ID mapping
# ---------------------------------------------------------------------------
#
# The frontend's paywall has three tiers (month / quart / year). Each
# maps to a Stripe Price (recurring product) configured in the dashboard.
# Env vars keep secrets out of code; they're set in .env.production
# alongside STRIPE_SECRET_KEY.
#
# If a tier's env is missing we treat it as not-yet-configured and 400
# the request rather than silently swap to a different tier.

_TIER_ENV: dict[str, str] = {
    'month': 'STRIPE_PRICE_PREMIUM_MONTH',
    'quart': 'STRIPE_PRICE_PREMIUM_QUARTER',
    'year':  'STRIPE_PRICE_PREMIUM_YEAR',
}


def _price_for_tier(tier_key: str) -> Optional[str]:
    env_var = _TIER_ENV.get(tier_key)
    if not env_var:
        return None
    return os.environ.get(env_var) or None


# ---------------------------------------------------------------------------
# POST /checkout/web {tier_key}
# ---------------------------------------------------------------------------

def post_checkout_web(s, req):
    """Create a Stripe Checkout session for the requested tier.

    Returns:
      {"url": "<stripe checkout url>"} on success
      'Tier not available', 400  if env price-id missing
      'Checkout not configured', 503 if STRIPE_SECRET_KEY unset
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Checkout not configured', 503

    tier_key = req.tier_key
    price_id = _price_for_tier(tier_key)
    if not price_id:
        return f"Tier '{tier_key}' not available", 400

    # success / cancel URLs default to the app's deployed domain. Configure
    # AHAVAH_WEB_BASE_URL in .env.production (e.g. https://ahavah.app).
    web_base = os.environ.get('AHAVAH_WEB_BASE_URL', 'https://ahavah.app').rstrip('/')

    try:
        session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            metadata={'user_id': str(s.person_id), 'tier_key': tier_key},
            # Mirror the same metadata onto the subscription itself so
            # downstream events (customer.subscription.updated, .deleted)
            # carry user_id directly without needing the
            # person.stripe_customer_id lookup. Belt-and-suspenders.
            subscription_data={
                'metadata': {'user_id': str(s.person_id), 'tier_key': tier_key},
            },
            client_reference_id=str(s.person_id),
            success_url=f'{web_base}/profile?subscription=success',
            cancel_url=f'{web_base}/paywall?subscription=cancel',
        )
    except Exception as e:
        logger.warning(f'Stripe Checkout session create failed: {e}')
        return 'Could not start checkout', 502

    return {'url': session.url}


# ---------------------------------------------------------------------------
# POST /webhooks/stripe-checkout
# ---------------------------------------------------------------------------
#
# Stripe-signed webhook for subscription lifecycle events. Auth is purely
# the Stripe-Signature header validated against STRIPE_WEBHOOK_SECRET_CHECKOUT.
# Handles:
#   - checkout.session.completed       → grant 'premium', stamp customer_id
#   - customer.subscription.updated    → re-grant if active, revoke if canceled
#   - customer.subscription.deleted    → revoke 'premium'
#   - invoice.payment_failed           → log only (Stripe retries auto)
#
# Idempotent via service.entitlements.record_event() — a Stripe retry
# (which can fire repeatedly until we 200) is detected and short-circuited
# without re-applying the entitlement change.

_PREMIUM_ENTITLEMENT = 'premium'


def _resolve_person_id(obj) -> Optional[int]:
    """Find person_id for an event object.

    Order of checks:
      1. obj.metadata.user_id          — set on Checkout session + sub
      2. obj.client_reference_id       — set on Checkout session only
      3. person.stripe_customer_id     — populated on first checkout completion
    """
    md = (obj.get('metadata') or {}) if isinstance(obj, dict) else {}
    uid = md.get('user_id') or obj.get('client_reference_id') if isinstance(obj, dict) else None
    if uid:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass

    customer_id = obj.get('customer') if isinstance(obj, dict) else None
    if not customer_id:
        return None

    from database import api_tx
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT id FROM person WHERE stripe_customer_id = %(c)s',
            dict(c=customer_id),
        ).fetchone()
    return row['id'] if row else None


def _stamp_customer_id(person_id: int, customer_id: str) -> None:
    """Idempotent — only writes if the column is currently NULL/different."""
    if not person_id or not customer_id:
        return
    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person
               SET stripe_customer_id = %(c)s
             WHERE id = %(id)s
               AND (stripe_customer_id IS NULL OR stripe_customer_id <> %(c)s)
            """,
            dict(id=person_id, c=customer_id),
        )


def _expiry_from_subscription(sub: dict, tier_key: Optional[str]) -> datetime:
    """Take the LATER of (Stripe's current_period_end, tier-default).
    `current_period_end` is unix seconds; absent for invoice-only events
    in which case we fall back to the tier-derived window."""
    now = datetime.now(timezone.utc)
    days = _TIER_EXPIRY_DAYS.get(tier_key or '', 31)
    fallback = now + timedelta(days=days)
    cpe = sub.get('current_period_end') if isinstance(sub, dict) else None
    if isinstance(cpe, (int, float)):
        stripe_exp = datetime.fromtimestamp(int(cpe), tz=timezone.utc)
        return max(stripe_exp, fallback)
    return fallback


def post_stripe_checkout_webhook():
    """Stripe-signed webhook for /webhooks/stripe-checkout."""
    stripe = _stripe()
    if stripe is None:
        return 'Webhook not configured', 503

    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET_CHECKOUT')
    if not webhook_secret:
        return 'Webhook secret not set', 503

    from flask import request  # local import — checkout module is also
                                # imported at boot before Flask is fully wired

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400

    try:
        event = json.loads(payload)
    except ValueError:
        return 'Invalid JSON', 400

    event_id = event.get('id') or ''
    event_type = event.get('type') or ''
    obj = ((event.get('data') or {}).get('object') or {})

    # Replay protection — Stripe retries unacknowledged webhooks up to
    # several times. record_event INSERTs into the append-only ledger
    # with ON CONFLICT DO NOTHING; if False, this is a replay.
    from service import entitlements
    person_id = _resolve_person_id(obj)
    if not entitlements.record_event(
        event_id=event_id,
        event_type=event_type,
        app_user_id=str(person_id or 'unknown'),
        payload=event,
    ):
        # Replay — already processed. 200 so Stripe stops retrying.
        return {'ok': True, 'replay': True}

    if not person_id:
        # No mapping yet — could be a customer.subscription.* event for
        # someone whose checkout.session.completed hasn't been processed.
        # Stripe orders events by occurrence so this is rare; logging-only
        # is the safest response (200 prevents retry storms).
        logger.warning(
            'stripe-checkout webhook %s (%s): could not resolve person_id',
            event_id, event_type,
        )
        return {'ok': True, 'unmatched': True}

    md = obj.get('metadata') or {}
    tier_key = md.get('tier_key')

    if event_type == 'checkout.session.completed':
        customer_id = obj.get('customer')
        if customer_id:
            _stamp_customer_id(person_id, customer_id)
        # On a checkout completion the subscription period info isn't on
        # the session itself — fetch the subscription to get the real
        # current_period_end. Falls back to tier-derived window on error.
        sub_id = obj.get('subscription')
        sub_obj: dict = {}
        if sub_id:
            try:
                sub_obj = stripe.Subscription.retrieve(sub_id).to_dict_recursive()
            except Exception:
                sub_obj = {}
        expires_at = _expiry_from_subscription(sub_obj, tier_key)
        entitlements.grant(person_id, _PREMIUM_ENTITLEMENT, expires_at=expires_at)
        return {'ok': True, 'granted': _PREMIUM_ENTITLEMENT}

    if event_type == 'customer.subscription.updated':
        # Active or trialing → keep premium; canceled/unpaid/past_due → revoke.
        status = obj.get('status') or ''
        if status in ('active', 'trialing'):
            expires_at = _expiry_from_subscription(obj, tier_key)
            entitlements.grant(person_id, _PREMIUM_ENTITLEMENT, expires_at=expires_at)
            return {'ok': True, 'kept': True, 'status': status}
        if status in ('canceled', 'unpaid', 'incomplete_expired'):
            entitlements.revoke(person_id, _PREMIUM_ENTITLEMENT)
            return {'ok': True, 'revoked': True, 'status': status}
        # past_due / incomplete: keep premium for now; user has a grace
        # window with Stripe before it auto-cancels. expire_stale cron
        # cleans up if subscription_expires_at passes.
        return {'ok': True, 'noop': True, 'status': status}

    if event_type == 'customer.subscription.deleted':
        entitlements.revoke(person_id, _PREMIUM_ENTITLEMENT)
        return {'ok': True, 'revoked': True}

    if event_type == 'invoice.payment_failed':
        # Log-only. Stripe will retry the invoice; if all retries fail it
        # eventually fires customer.subscription.deleted, which IS handled.
        logger.info(
            'stripe-checkout invoice.payment_failed for person_id=%s', person_id,
        )
        return {'ok': True, 'logged': True}

    # Other event types — accept (200) to suppress retries; nothing to do.
    return {'ok': True, 'ignored': event_type}
