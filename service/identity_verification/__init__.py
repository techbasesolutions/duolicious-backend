"""
Phase 3 Task 3.1 — verification-tier promotion (Stripe Identity webhook +
session-creation endpoint).

Three tiers (per migration 0003):
  bronze : the upstream Duolicious fork's existing selfie+gender+age+ethnicity check (set
           elsewhere by service.person.post_verify when the legacy flow
           passes — this module does NOT touch bronze).
  silver : AWS Amplify Face Liveness (Phase 3 Task 3.2 Step 3 — separate
           webhook flow, not implemented in this module).
  gold   : Stripe Identity (this module).

This module is intentionally side-effect-free at import time. Stripe is
configured lazily on first use so the api can boot in dev without
STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET set — the routes will just
reject calls with a 503 in that case (the same opt-in pattern as the
DeepL primitive in Phase 2).

Endpoints exposed via service/api/__init__.py:
  POST /verification/start-id-flow
       → creates a Stripe Identity VerificationSession for the caller,
         returns {"client_secret": "vs_..."} for the frontend to open.
  POST /webhooks/stripe-identity
       → Stripe-signed webhook. On `verification_session.verified`,
         promotes the linked person to verification_level='gold' and
         records the issuing country + verification timestamp.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from flask import request

from database import api_tx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy Stripe configuration
# ---------------------------------------------------------------------------

_stripe_init_attempted = False
_stripe_module = None
_webhook_secret: Optional[str] = None


def _stripe():
    """Returns the configured stripe module, or None if STRIPE_SECRET_KEY is unset."""
    global _stripe_init_attempted, _stripe_module, _webhook_secret
    if _stripe_init_attempted:
        return _stripe_module
    _stripe_init_attempted = True

    api_key = os.environ.get('STRIPE_SECRET_KEY')
    if not api_key:
        logger.info('STRIPE_SECRET_KEY not set; Stripe Identity flow disabled')
        return None

    try:
        import stripe
        stripe.api_key = api_key
        _stripe_module = stripe
        # Suffix matches the checkout module's `STRIPE_WEBHOOK_SECRET_CHECKOUT`
        # convention. Falling back to the un-suffixed name keeps any older
        # deploys booting if both are set / only the legacy name is set.
        _webhook_secret = (
            os.environ.get('STRIPE_WEBHOOK_SECRET_IDENTITY')
            or os.environ.get('STRIPE_WEBHOOK_SECRET')
        )
        if not _webhook_secret:
            logger.warning(
                'STRIPE_SECRET_KEY set but STRIPE_WEBHOOK_SECRET_IDENTITY '
                'unset; incoming Identity webhooks will be rejected'
            )
        logger.info('Stripe Identity client initialized')
    except Exception as e:
        logger.warning(f'Stripe init failed: {e}')
        _stripe_module = None
    return _stripe_module


# ---------------------------------------------------------------------------
# Promotion helper (pure DB — testable without Stripe)
# ---------------------------------------------------------------------------

VALID_LEVELS = {'none', 'bronze', 'silver', 'gold'}


def promote_user(person_id: int, level: str, country: Optional[str] = None) -> bool:
    """Sets verification_level on `person_id`. Idempotent — re-promoting to
    the same level is a no-op. Promoting *down* is rejected (returns False),
    so a user who's already gold can't be silently demoted by a stale silver
    webhook arriving after a gold one.

    Returns True if the row was updated, False otherwise (already at or
    above the requested level, or person not found).
    """
    if level not in VALID_LEVELS:
        raise ValueError(f'level must be one of {VALID_LEVELS}, got {level!r}')

    rank = {'none': 0, 'bronze': 1, 'silver': 2, 'gold': 3}
    new_rank = rank[level]

    with api_tx() as tx:
        row = tx.execute(
            'SELECT ahavah_verification_tier FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
        if not row:
            return False

        # Migration 0003 added `ahavah_verification_tier` ENUM
        # ('none','bronze','silver','gold') as the Phase W tier column.
        # The earlier code addressed `verification_level` (text), which
        # never existed — every webhook silently 500'd. The legacy
        # `verification_level_id` (integer FK to the upstream
        # Duolicious lookup) is updated separately by the Bronze cron
        # at service/cron/verificationjobrunner.
        current = row['ahavah_verification_tier']
        if rank.get(current, 0) >= new_rank:
            # Don't allow demotion or no-op rewrites.
            return False

        if level == 'gold':
            tx.execute(
                """
                UPDATE person
                   SET ahavah_verification_tier = %(level)s::ahavah_verification_tier,
                       id_verified_country      = %(country)s,
                       id_verified_at           = NOW()
                 WHERE id = %(id)s
                """,
                dict(id=person_id, level=level, country=country),
            )
        else:
            tx.execute(
                """
                UPDATE person
                   SET ahavah_verification_tier = %(level)s::ahavah_verification_tier
                 WHERE id = %(id)s
                """,
                dict(id=person_id, level=level),
            )
    return True


# ---------------------------------------------------------------------------
# POST /verification/start-id-flow
# ---------------------------------------------------------------------------

def post_start_id_flow(s):
    """Creates a Stripe Identity VerificationSession for `s.person_id` and
    returns its client_secret. The frontend opens that in expo-web-browser.

    Stripe links the session back to us via metadata.user_id, which the
    webhook then reads to promote the right user.
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    stripe = _stripe()
    if stripe is None:
        return 'Identity verification is not configured', 503

    try:
        session = stripe.identity.VerificationSession.create(
            type='document',
            metadata={'user_id': str(s.person_id)},
            options={
                'document': {
                    'require_matching_selfie': True,
                    'require_live_capture': True,
                    'require_id_number': False,
                },
            },
        )
    except Exception as e:
        logger.warning(f'Stripe Identity session create failed: {e}')
        return 'Could not start verification', 502

    return {
        'session_id':    session.id,
        'client_secret': session.client_secret,
        'url':           getattr(session, 'url', None),
    }


# ---------------------------------------------------------------------------
# POST /webhooks/stripe-identity
# ---------------------------------------------------------------------------

def post_stripe_identity_webhook():
    """Stripe-signed webhook endpoint. No app auth — Stripe's signature is
    the auth. We look up the user via the session's metadata.user_id and
    promote them to 'gold' on `verification_session.verified`.

    Other event types are accepted (200 response) but logged-only:
      - `requires_input`  — user needs to retry; no DB write
      - `canceled`        — user abandoned; no DB write (could surface in admin)
      - `processing`      — pending; no DB write
    """
    stripe = _stripe()
    if stripe is None or _webhook_secret is None:
        return 'Webhook not configured', 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        # Signature verification only — we discard the StripeObject
        # downstream because newer SDK versions trigger __getattr__('object')
        # on the discriminator field and raise AttributeError when test
        # payloads omit it. Plain json.loads gives predictable dict semantics.
        stripe.Webhook.construct_event(payload, sig_header, _webhook_secret)
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400

    try:
        event_data = json.loads(
            payload.decode('utf-8') if isinstance(payload, bytes) else payload
        )
    except (ValueError, UnicodeDecodeError):
        return 'Invalid payload', 400

    event_type = event_data.get('type', '')
    obj = event_data.get('data', {}).get('object', {}) or {}

    if event_type == 'identity.verification_session.verified':
        meta = obj.get('metadata') or {}
        user_id = _coerce_int(meta.get('user_id'))
        if not user_id:
            logger.warning('Stripe verified event missing metadata.user_id')
            return {'received': True, 'promoted': False, 'reason': 'no_user_id'}

        verified_outputs = obj.get('verified_outputs') or {}
        document = verified_outputs.get('document') or {}
        country = document.get('issuing_country')

        promoted = promote_user(user_id, 'gold', country=country)
        return {'received': True, 'promoted': promoted}

    elif event_type in (
        'identity.verification_session.requires_input',
        'identity.verification_session.canceled',
        'identity.verification_session.processing',
        'identity.verification_session.created',
    ):
        # Acknowledged but no DB change. Future: surface to admin queue.
        return {'received': True, 'promoted': False}

    # Unknown event types — return 200 so Stripe doesn't retry forever, but log.
    logger.info(f'Unhandled Stripe Identity event: {event_type}')
    return {'received': True, 'promoted': False}


def _coerce_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
