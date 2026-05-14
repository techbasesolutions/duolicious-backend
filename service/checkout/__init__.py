"""
Phase W cutover — Stripe Checkout for web subscription purchases.

Mirrors the structure of service/identity_verification: lazy-init Stripe,
same 503-on-not-configured opt-in pattern. Endpoint creates a Checkout
session bound to a Premium price (env-keyed per tier) and returns the
Stripe-hosted URL the frontend redirects to.

Phase 5 enhancement (deferred): the matching webhook
/webhooks/stripe-checkout listens for `checkout.session.completed` and
upgrades the user's entitlement (plan: write to person.has_premium plus
a subscription record). Until that lands, completed payments don't
flip the user's premium state — the checkout flow itself works, but
provisioning needs the webhook handler to follow.

Endpoints exposed via service/api/__init__.py:
  POST /checkout/web {tier_key}
       → creates a Stripe Checkout session for `tier_key` ('month' /
         'quart' / 'year'), returns {"url": "https://checkout.stripe.com/…"}
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


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
            success_url=f'{web_base}/profile?subscription=success',
            cancel_url=f'{web_base}/paywall?subscription=cancel',
        )
    except Exception as e:
        logger.warning(f'Stripe Checkout session create failed: {e}')
        return 'Could not start checkout', 502

    return {'url': session.url}
