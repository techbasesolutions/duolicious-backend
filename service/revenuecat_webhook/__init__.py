"""
Phase 5 Task 5.2 — RevenueCat webhook handler.

RevenueCat does not sign payloads cryptographically; it authenticates
webhooks via a shared bearer token configured in the RevenueCat dashboard
under "Authorization Header". We compare against RC_WEBHOOK_AUTH at
runtime — opt-in, like every other paid integration in this project.

Public surface:
  post_revenuecat_webhook()    Flask handler. Wired into service/api/__init__.py.

Event mapping (per Task 5.2 Step 3):

  INITIAL_PURCHASE / RENEWAL / PRODUCT_CHANGE
      → grant(entitlement, expires_at)
  EXPIRATION
      → revoke(entitlement)
  CANCELLATION / NON_RENEWING_PURCHASE
      → grant only (entitlement remains until EXPIRATION)
  BILLING_ISSUE
      → ledger-only (no entitlement change; user keeps access during grace)
  TRANSFER / SUBSCRIPTION_PAUSED / SUBSCRIBER_ALIAS / etc.
      → ledger-only

Replay protection (F2): a cheap read-only SELECT pre-checks entitlement_event
for the event id before any DB mutation runs. The authoritative latch, the
INSERT ... ON CONFLICT DO NOTHING into entitlement_event, commits LAST,
after the grant/revoke effect. This way a crash between the effect and the
latch makes RC retry the delivery and the retry re-applies cleanly (grant
and revoke are idempotent) instead of the latch permanently masking a paid
effect that never actually ran.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from flask import request

from service.entitlements import grant, record_event, revoke

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _expected_auth() -> Optional[str]:
    """Returns the bearer-token value RC should send. None means the webhook
    is not configured — handler returns 503 in that case."""
    secret = os.environ.get('RC_WEBHOOK_AUTH')
    return f'Bearer {secret}' if secret else None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

GRANT_TYPES = {
    'INITIAL_PURCHASE',
    'RENEWAL',
    'PRODUCT_CHANGE',
    'NON_RENEWING_PURCHASE',
    'UNCANCELLATION',
}
REVOKE_TYPES = {'EXPIRATION'}
LEDGER_ONLY_TYPES = {
    'CANCELLATION',         # access remains until EXPIRATION fires
    'BILLING_ISSUE',
    'SUBSCRIPTION_PAUSED',
    'TRANSFER',
    'SUBSCRIBER_ALIAS',
    'TEST',
}


def _already_processed(event_id: str) -> bool:
    """Read-only replay pre-check, cheap and safe to repeat unlimited
    times. The authoritative latch is record_event(), called LAST after
    the grant/revoke effect has applied (F2 ordering contract: effects
    first, latch last, so a crash in between makes RC retry and the retry
    re-applies cleanly instead of silently losing the effect)."""
    from database import api_tx
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT 1 FROM entitlement_event WHERE event_id = %(eid)s',
            dict(eid=event_id),
        ).fetchone()
    return row is not None


def post_revenuecat_webhook():
    """Flask handler. No app-session auth — bearer header is the auth."""
    expected = _expected_auth()
    if expected is None:
        return 'Webhook not configured', 503

    auth_header = request.headers.get('Authorization', '')
    if auth_header != expected:
        return 'Unauthorized', 401

    raw = request.get_data()
    try:
        body = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError):
        return 'Invalid payload', 400

    event = body.get('event') or {}
    event_id = event.get('id')
    event_type = event.get('type', '')
    app_user_id = event.get('app_user_id') or ''
    entitlement_id = event.get('entitlement_id') or 'premium'
    expires_iso = event.get('expiration_iso') or event.get('expiration_at_iso')

    if not event_id:
        logger.warning(f'RC webhook missing event.id (type={event_type})')
        return 'Missing event.id', 400

    person_id = _coerce_int(app_user_id)

    # 1. Replay pre-check, read-only and safe to repeat. The authoritative
    #    latch (record_event) commits LAST, after the grant/revoke effect
    #    below (F2). grant()/revoke() are each idempotent and open their
    #    own tx, so a crash between the effect and the latch double-applies
    #    harmlessly on RC's retry instead of eating the effect forever.
    if _already_processed(event_id):
        return {'received': True, 'replay': True, 'applied': False}

    if not person_id:
        logger.warning(f'RC event {event_id} (type {event_type}): missing/invalid app_user_id={app_user_id!r}')
        # No effect was possible without a person_id, so latch immediately
        # and keep the malformed event auditable without reprocessing it.
        record_event(
            event_id=event_id,
            event_type=event_type,
            app_user_id=str(app_user_id),
            payload=event,
        )
        return {'received': True, 'replay': False, 'applied': False, 'reason': 'no_user_id'}

    # 2. Apply, THEN latch.
    if event_type in GRANT_TYPES:
        expires = _parse_iso(expires_iso)
        grant(person_id, entitlement_id, expires_at=expires)
        record_event(
            event_id=event_id,
            event_type=event_type,
            app_user_id=str(app_user_id),
            payload=event,
        )
        return {'received': True, 'replay': False, 'applied': True, 'action': 'grant'}

    if event_type in REVOKE_TYPES:
        revoke(person_id, entitlement_id)
        record_event(
            event_id=event_id,
            event_type=event_type,
            app_user_id=str(app_user_id),
            payload=event,
        )
        return {'received': True, 'replay': False, 'applied': True, 'action': 'revoke'}

    if event_type in LEDGER_ONLY_TYPES:
        record_event(
            event_id=event_id,
            event_type=event_type,
            app_user_id=str(app_user_id),
            payload=event,
        )
        return {'received': True, 'replay': False, 'applied': True, 'action': 'ledger_only'}

    logger.info(f'RC unhandled event_type={event_type} (event_id={event_id}); ledger-only')
    record_event(
        event_id=event_id,
        event_type=event_type,
        app_user_id=str(app_user_id),
        payload=event,
    )
    return {'received': True, 'replay': False, 'applied': True, 'action': 'unknown_ledger_only'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_iso(s) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        # RC sends `2027-05-08T00:00:00Z` style. fromisoformat in 3.11
        # doesn't accept `Z`, so swap to +00:00.
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
