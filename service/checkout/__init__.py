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
# Token-bundle SKU mapping (Phase 2 token economy)
# ---------------------------------------------------------------------------
#
# One-shot token purchases. Each SKU maps to a Stripe Price (one-time, not
# recurring) configured in the dashboard. Same env-missing → 400 rule as the
# subscription tiers above. The webhook below mirrors `SKU_TO_COUNT` to
# decide how many tokens to credit on session.mode == 'payment'.

_SKU_ENV: dict[str, str] = {
    'single':  'STRIPE_PRICE_TOKENS_SINGLE',
    'starter': 'STRIPE_PRICE_TOKENS_STARTER',
    'plus':    'STRIPE_PRICE_TOKENS_PLUS',
    'pro':     'STRIPE_PRICE_TOKENS_PRO',
}

SKU_TO_COUNT: dict[str, int] = {
    'single':  1,
    'starter': 10,
    'plus':    22,
    'pro':     50,
}


# ---------------------------------------------------------------------------
# Subscription tier → monthly token stipend (Phase 8 — monetization-tokens v1)
# ---------------------------------------------------------------------------
#
# Premium subscribers get a monthly token stipend they can spend on
# super-likes, boosts, and extra likers reveals. Counts are intentionally
# inversely-loaded: longer commitments yield more tokens per renewal
# cycle. The webhook credits these tokens on both initial subscription
# (checkout.session.completed, mode='subscription') AND each renewal
# (invoice.payment_succeeded, billing_reason='subscription_cycle').
#
# Idempotency is enforced at the ledger level by checking for an existing
# row with metadata->>'stripe_session_id' (initial) or
# metadata->>'stripe_invoice_id' (renewal). Stripe retries cannot
# double-credit.

SUBSCRIPTION_TIER_TO_STIPEND: dict[str, int] = {
    'month': 10,
    'quart': 12,
    'year':  15,
}


def _price_for_sku(sku: str) -> Optional[str]:
    env_var = _SKU_ENV.get(sku)
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
# POST /checkout/tokens {sku}
# ---------------------------------------------------------------------------
#
# One-shot token bundle purchase (Phase 2). Mirrors post_checkout_web but
# with mode='payment' instead of 'subscription'. The matching webhook
# branch (mode == 'payment' below) credits token_ledger on completion.
#
# `client_reference_id` carries the person UUID (string) so the webhook
# can credit the right user without a customer_id lookup. We also stash
# sku in session.metadata so the webhook can map back to a token count
# independent of the Price-ID (Price IDs change between test/live mode).

def post_checkout_tokens(s, req):
    """Create a Stripe Checkout session for a token-bundle SKU.

    Returns:
      {"url": "<stripe checkout url>"} on success
      {"error": "unknown_sku"}, 400      if SKU env price-id missing
      'Not authorized', 401              if no session
      'Checkout not configured', 503     if STRIPE_SECRET_KEY unset
      'Could not start checkout', 502    on Stripe API failure
    """
    if not s or not s.person_id or not s.person_uuid:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Checkout not configured', 503

    sku = req.sku
    price_id = _price_for_sku(sku)
    if not price_id:
        return {'error': 'unknown_sku'}, 400

    web_base = os.environ.get('AHAVAH_WEB_BASE_URL', 'https://ahavah.app').rstrip('/')

    try:
        session = stripe.checkout.Session.create(
            mode='payment',
            line_items=[{'price': price_id, 'quantity': 1}],
            metadata={
                'user_id':   str(s.person_id),
                'user_uuid': str(s.person_uuid),
                'sku':       sku,
            },
            # Use person UUID — that's what token_ledger.person_id keys on.
            client_reference_id=str(s.person_uuid),
            success_url=f'{web_base}/profile/tokens?purchase=success',
            cancel_url=f'{web_base}/profile/tokens?purchase=cancel',
        )
    except Exception as e:
        logger.warning(f'Stripe Checkout (tokens) session create failed: {e}')
        return 'Could not start checkout', 502

    return {'url': session.url}


# ---------------------------------------------------------------------------
# GET /billing-portal
# ---------------------------------------------------------------------------
#
# Stripe Customer Portal session — drops the user into Stripe's hosted
# subscription-management UI (update card, view invoices, cancel,
# resume). Without this, every "I want to cancel" or "my card expired"
# becomes a support ticket. Requires the user to have completed at
# least one paid Checkout (which stamps person.stripe_customer_id via
# the webhook); free users get 400.

# Stripe Customer Portal flow_data types we deep-link to. Each maps a
# specific in-app action button straight to the matching Stripe flow so the
# user lands on (e.g.) the cancel screen instead of the generic portal home.
# There is no 'pause' flow type — pause stays on the generic portal.
_PORTAL_FLOW_TYPES = frozenset({
    'subscription_update',
    'subscription_cancel',
    'payment_method_update',
})


# The subscription_update / subscription_cancel portal flows REQUIRE the
# target subscription id in flow_data (Stripe 400s without it); the
# payment_method_update flow + the generic portal do not.
_SUB_SCOPED_FLOWS = frozenset({'subscription_update', 'subscription_cancel'})


def _customer_id_for(person_id):
    from database import api_tx
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT stripe_customer_id FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
    return (row or {}).get('stripe_customer_id')


def _active_subscription_id(stripe, customer_id):
    """First non-terminal subscription id for the customer, or None.
    Prefers active/trialing/past_due over canceled ones."""
    try:
        subs = stripe.Subscription.list(customer=customer_id, status='all', limit=10)
    except Exception as e:
        logger.warning(f'Stripe subscription list (for flow) failed: {e}')
        return None
    data = _g(subs, 'data') or []
    live = {'active', 'trialing', 'past_due', 'unpaid', 'paused'}
    chosen = None
    for sub in data:
        sid = _g(sub, 'id')
        if not sid:
            continue
        if _g(sub, 'status') in live:
            return sid
        chosen = chosen or sid
    return chosen


def get_billing_portal(s, flow=None):
    """Return {'url': '<stripe portal URL>'} or an error tuple.

    `flow` (optional) deep-links into a specific Customer Portal flow when it
    is one of _PORTAL_FLOW_TYPES; otherwise the generic portal home is used.

    Status codes:
      200 — success
      401 — not signed in
      400 — user has no Stripe customer record (never subscribed)
      503 — Stripe not configured (STRIPE_SECRET_KEY unset)
      502 — Stripe API call failed
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Billing portal not configured', 503

    customer_id = _customer_id_for(s.person_id)
    if not customer_id:
        return 'No active subscription', 400

    web_base = os.environ.get('AHAVAH_WEB_BASE_URL', 'https://ahavah.app').rstrip('/')

    kwargs = dict(customer=customer_id, return_url=f'{web_base}/profile')
    if flow in _PORTAL_FLOW_TYPES:
        flow_data = {'type': flow}
        if flow in _SUB_SCOPED_FLOWS:
            # These flows need the target subscription id or Stripe 400s.
            sub_id = _active_subscription_id(stripe, customer_id)
            if not sub_id:
                return 'No active subscription to manage', 400
            flow_data[flow] = {'subscription': sub_id}
        kwargs['flow_data'] = flow_data

    try:
        session = stripe.billing_portal.Session.create(**kwargs)
    except Exception as e:
        logger.warning(f'Stripe billing portal create failed: {e}')
        return 'Could not start billing portal', 502

    return {'url': session.url}


# ---------------------------------------------------------------------------
# GET /billing/subscription + GET /billing/invoices — native read surfaces.
# These feed the rebuilt billing page real data (replacing the old
# PLACEHOLDER_SUBSCRIPTION). Read-only: no money mutation here.
# ---------------------------------------------------------------------------

def _g(obj, key, default=None):
    """Key-or-attribute getter tolerant of Stripe objects, dicts, and mocks.

    Dict access is tried FIRST: Stripe's StripeObject is a dict subclass, so
    `getattr(obj, 'items')` would return the built-in dict.items method rather
    than the field value. Reading the key off the mapping avoids that
    collision (also covers plain dicts used in tests)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        val = obj.get(key, None)
    else:
        val = getattr(obj, key, None)
    return default if val is None else val


def _price_label(price) -> str:
    """Build '$8.99 / month' from a Stripe price object. Tolerant of missing
    fields — falls back to a plain dollar amount or empty string."""
    amount = _g(price, 'unit_amount')
    if amount is None:
        return ''
    recurring = _g(price, 'recurring')
    interval = _g(recurring, 'interval')
    cents = amount % 100
    dollars = f'${amount / 100:.2f}' if cents else f'${amount // 100}'
    return f'{dollars} / {interval}' if interval else dollars


def get_subscription(s):
    """Current subscription summary, or {'status': 'none'}.

    Status codes: 200, 401 (no session), 503 (Stripe unconfigured),
    502 (Stripe call failed).
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Billing not configured', 503

    customer_id = _customer_id_for(s.person_id)
    if not customer_id:
        return {'status': 'none'}

    try:
        subs = stripe.Subscription.list(
            customer=customer_id,
            status='all',
            limit=1,
            expand=['data.default_payment_method', 'data.items.data.price'],
        )
    except Exception as e:
        logger.warning(f'Stripe subscription list failed: {e}')
        return 'Could not load subscription', 502

    data = _g(subs, 'data') or []
    if not data:
        return {'status': 'none'}

    sub = data[0]
    item_data = _g(_g(sub, 'items'), 'data') or []
    price = _g(item_data[0], 'price') if item_data else None
    card = _g(_g(sub, 'default_payment_method'), 'card')

    return {
        'status': _g(sub, 'status', 'none'),
        'plan_label': 'Premium',
        'price_label': _price_label(price),
        'current_period_end': _g(sub, 'current_period_end'),
        'cancel_at_period_end': bool(_g(sub, 'cancel_at_period_end', False)),
        'card_brand': _g(card, 'brand'),
        'card_last4': _g(card, 'last4'),
    }


def get_invoices(s):
    """Up to 12 recent invoices with hosted + PDF links. 401/503/502 as above.
    Empty list when the user has no Stripe customer record."""
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Billing not configured', 503

    customer_id = _customer_id_for(s.person_id)
    if not customer_id:
        return {'invoices': []}

    try:
        invoices = stripe.Invoice.list(customer=customer_id, limit=12)
    except Exception as e:
        logger.warning(f'Stripe invoice list failed: {e}')
        return 'Could not load invoices', 502

    data = _g(invoices, 'data') or []
    out = []
    for inv in data:
        amount = _g(inv, 'amount_paid')
        if amount is None:
            amount = _g(inv, 'amount_due', 0)
        currency = (_g(inv, 'currency', 'usd') or 'usd').upper()
        out.append({
            'id': _g(inv, 'id'),
            'created': _g(inv, 'created'),
            'amount_label': f'${amount / 100:.2f} {currency}',
            'status': _g(inv, 'status'),
            'hosted_invoice_url': _g(inv, 'hosted_invoice_url'),
            'invoice_pdf': _g(inv, 'invoice_pdf'),
        })
    return {'invoices': out}


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


# ---------------------------------------------------------------------------
# Token-purchase webhook handler (mode=payment branch)
# ---------------------------------------------------------------------------
#
# Two layers of idempotency:
#   1. service.entitlements.record_event() in the outer webhook dedupes by
#      Stripe event_id (so a literal retry of the same event is a no-op).
#   2. This function ALSO checks token_ledger for an existing row with
#      metadata->>'stripe_session_id' = <session_id>. That's a belt-and-
#      suspenders guard against the (rare) case of two different events
#      referencing the same Checkout session — e.g. a manual replay of a
#      different event type with the same `id` payload swapped. The plan
#      mandates this ledger-level check explicitly.

def _handle_token_purchase(session: dict) -> dict:
    """Credit token_ledger for a completed mode=payment Checkout session.

    Reads:
      session['id']                    — Stripe session id (idempotency key)
      session['client_reference_id']   — person UUID (set on session create)
      session['metadata']['sku']       — bundle SKU ('single' | ... | 'pro')
      session['amount_total']          — paid amount in cents (for audit)

    Returns the dict the webhook should respond with (Stripe always 200).
    """
    from database import api_tx
    from service.tokens import credit

    session_id   = session.get('id') or ''
    md           = session.get('metadata') or {}
    sku          = md.get('sku') or ''
    person_uuid  = session.get('client_reference_id') or md.get('user_uuid') or ''
    amount_total = session.get('amount_total')

    token_count = SKU_TO_COUNT.get(sku)
    if not token_count:
        logger.warning(
            'token-purchase webhook: unknown sku=%r session_id=%r',
            sku, session_id,
        )
        return {'ok': True, 'ignored': 'unknown_sku'}

    if not person_uuid or not session_id:
        logger.warning(
            'token-purchase webhook: missing person_uuid or session_id '
            '(person_uuid=%r session_id=%r)', person_uuid, session_id,
        )
        return {'ok': True, 'ignored': 'missing_ref'}

    # Ledger-level idempotency — guard against re-crediting if this same
    # session_id was already processed under a different event_id.
    with api_tx() as tx:
        existing = tx.execute(
            """
            SELECT 1 FROM token_ledger
             WHERE metadata->>'stripe_session_id' = %(sid)s
             LIMIT 1
            """,
            dict(sid=session_id),
        ).fetchone()
        if existing:
            return {'ok': True, 'replay': True, 'session_id': session_id}

        credit(
            tx, person_uuid, token_count,
            reason='purchase',
            metadata={
                'stripe_session_id': session_id,
                'sku': sku,
                'amount_cents': amount_total,
            },
        )

    return {'ok': True, 'credited': token_count, 'sku': sku}


def _credit_subscription_stipend(
    *,
    person_uuid: str,
    tier_key: str,
    idempotency_key_field: str,
    idempotency_key_value: str,
    extra_metadata: Optional[dict] = None,
) -> dict:
    """Idempotently credit a subscription stipend.

    `idempotency_key_field` is either 'stripe_session_id' (initial
    subscription) or 'stripe_invoice_id' (renewal). We dedupe by checking
    token_ledger for an existing 'subscription_stipend' row carrying the
    same value in that metadata key. Belt-and-suspenders alongside the
    outer record_event() dedupe.
    """
    from database import api_tx
    from service.tokens import credit

    stipend = SUBSCRIPTION_TIER_TO_STIPEND.get(tier_key or '')
    if not stipend:
        logger.warning(
            'subscription stipend: unknown tier_key=%r (%s=%s)',
            tier_key, idempotency_key_field, idempotency_key_value,
        )
        return {'ok': True, 'ignored': 'unknown_tier'}
    if not person_uuid or not idempotency_key_value:
        logger.warning(
            'subscription stipend: missing person_uuid or %s',
            idempotency_key_field,
        )
        return {'ok': True, 'ignored': 'missing_ref'}

    with api_tx() as tx:
        # Serialize concurrent webhook deliveries carrying the same
        # idempotency value so the SELECT-then-credit below can't race two
        # callers past the existence check and double-credit the stipend.
        # Xact-scoped — released on commit/rollback.
        tx.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%(v)s))",
            dict(v=idempotency_key_value),
        )
        existing = tx.execute(
            f"""
            SELECT 1 FROM token_ledger
             WHERE reason = 'subscription_stipend'
               AND metadata->>'{idempotency_key_field}' = %(v)s
             LIMIT 1
            """,
            dict(v=idempotency_key_value),
        ).fetchone()
        if existing:
            return {'ok': True, 'replay': True, idempotency_key_field: idempotency_key_value}

        metadata = {
            'tier_key': tier_key,
            idempotency_key_field: idempotency_key_value,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        credit(
            tx, person_uuid, stipend,
            reason='subscription_stipend',
            metadata=metadata,
        )
    return {'ok': True, 'credited': stipend, 'tier_key': tier_key}


def _person_uuid_for_id(person_id: int) -> Optional[str]:
    """Look up person.uuid (string) for a numeric person_id."""
    if not person_id:
        return None
    from database import api_tx
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT uuid::text AS uuid FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
    return row['uuid'] if row else None


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

    # Phase 2 — token-bundle one-shot purchases discriminate by session.mode.
    # Subscription Checkouts (mode='subscription') fall through to the
    # entitlement branches below; token bundles (mode='payment') are handled
    # here and return early. Only triggers on checkout.session.completed —
    # subscription.* events have no `mode` field.
    if event_type == 'checkout.session.completed' and obj.get('mode') == 'payment':
        return _handle_token_purchase(obj)

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

        # Phase 8 — credit the monthly token stipend on initial subscription.
        # Idempotent per checkout session id; renewal cycles are handled
        # below in the invoice.payment_succeeded branch. Failures here MUST
        # NOT 500 the webhook (Stripe would retry forever); we log and 200.
        try:
            person_uuid = _person_uuid_for_id(person_id)
            if person_uuid:
                _credit_subscription_stipend(
                    person_uuid=person_uuid,
                    tier_key=tier_key or '',
                    idempotency_key_field='stripe_session_id',
                    idempotency_key_value=obj.get('id') or '',
                )
        except Exception as e:
            logger.warning(
                'subscription stipend credit failed for person_id=%s: %s',
                person_id, e,
            )
        return {'ok': True, 'granted': _PREMIUM_ENTITLEMENT}

    if event_type == 'invoice.payment_succeeded':
        # Renewal stipend. Only act on subscription_cycle invoices (i.e.,
        # automatic renewals); the FIRST invoice of a brand-new subscription
        # is billing_reason='subscription_create' and is handled by the
        # checkout.session.completed branch above. Idempotent per invoice id.
        if obj.get('billing_reason') != 'subscription_cycle':
            return {'ok': True, 'ignored': 'non_renewal'}
        sub_id = obj.get('subscription')
        if not sub_id:
            return {'ok': True, 'ignored': 'no_subscription'}
        try:
            sub = stripe.Subscription.retrieve(sub_id)
            sub_dict = sub.to_dict_recursive() if hasattr(sub, 'to_dict_recursive') else dict(sub)
        except Exception as e:
            logger.warning(
                'invoice.payment_succeeded: could not retrieve sub %s: %s',
                sub_id, e,
            )
            return {'ok': True, 'ignored': 'sub_retrieve_failed'}
        sub_md = sub_dict.get('metadata') or {}
        renewal_tier = sub_md.get('tier_key') or ''
        renewal_person_uuid = sub_md.get('person_uuid') or sub_md.get('user_uuid')
        if not renewal_person_uuid:
            # Fall back to person_id lookup → uuid translation.
            renewal_person_uuid = _person_uuid_for_id(person_id)
        return _credit_subscription_stipend(
            person_uuid=renewal_person_uuid or '',
            tier_key=renewal_tier,
            idempotency_key_field='stripe_invoice_id',
            idempotency_key_value=obj.get('id') or '',
            extra_metadata={'stripe_subscription_id': sub_id},
        )

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
        # Phase 8: cancellation revokes the premium entitlement and clears
        # the subscription expiry stamp. It DOES NOT touch token_ledger —
        # stipend tokens already credited are the user's property; canceling
        # a subscription does not retroactively void unspent tokens (same
        # rule as one-shot bundles). The frontend's quota / spend gates
        # consult token_ledger directly, so an ex-subscriber can still
        # spend their remaining balance until it hits zero.
        entitlements.revoke(person_id, _PREMIUM_ENTITLEMENT)
        from database import api_tx
        with api_tx() as tx:
            tx.execute(
                'UPDATE person SET subscription_expires_at = NULL WHERE id = %(id)s',
                dict(id=person_id),
            )
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


