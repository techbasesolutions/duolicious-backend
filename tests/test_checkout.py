"""
Phase 2 — Stripe Checkout for token bundles.

Tests POST /checkout/tokens (creates a Stripe session for a bundle SKU)
and the webhook branch that credits token_ledger on session.mode=payment.

Plan deviation: the plan referenced `http`, `person`, `session_token`
fixtures that don't exist. We mirror tests/test_tokens.py's local-fixture
pattern (real `person` + `duo_session` rows inserted in setup, cleaned up
on teardown). Stripe is monkeypatched at module-attribute level via the
lazy `_stripe()` cache in service.checkout — no live API calls.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Local fixtures — copied from tests/test_tokens.py (intentional duplication,
# the plan calls for per-test-file fixtures rather than a shared conftest).
# ---------------------------------------------------------------------------

@pytest.fixture
def person_uuid():
    """Insert a minimal `person` row and return {uuid, id}. Cleaned up after."""
    from database import api_tx

    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly, location_long_friendly, unit_id
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere', 'somewhere, nowhere', (SELECT id FROM unit LIMIT 1)
            )
            RETURNING uuid::text AS uuid, id
            """,
            dict(email=f'co-{uuid4()}@example.com'),
        ).fetchone()

    yield row

    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


@pytest.fixture
def session_token(person_uuid):
    """Create a duo_session row for the test person and return its token."""
    from database import api_tx
    tok = secrets.token_hex(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%s, %s, %s, TRUE, '123456')
            """,
            (tok_hash, f'co-session-{person_uuid["id"]}@example.com',
             person_uuid['id']),
        )
    return tok


@pytest.fixture
def fake_stripe(monkeypatch):
    """Force service.checkout._stripe() to return a MagicMock so the route
    never hits the real Stripe API. Also pre-loads the 4 SKU env vars so
    `_price_for_sku` returns a non-None placeholder."""
    import service.checkout as co

    fake = MagicMock(name='stripe_module')
    fake.checkout.Session.create.return_value = SimpleNamespace(
        url='https://checkout.stripe.com/c/pay/cs_test_fake',
    )
    monkeypatch.setattr(co, '_stripe_init_attempted', True)
    monkeypatch.setattr(co, '_stripe_module', fake)

    monkeypatch.setenv('STRIPE_PRICE_TOKENS_SINGLE',  'price_test_single')
    monkeypatch.setenv('STRIPE_PRICE_TOKENS_STARTER', 'price_test_starter')
    monkeypatch.setenv('STRIPE_PRICE_TOKENS_PLUS',    'price_test_plus')
    monkeypatch.setenv('STRIPE_PRICE_TOKENS_PRO',     'price_test_pro')
    monkeypatch.setenv('STRIPE_SECRET_KEY',           'sk_test_fake')

    return fake


# ---------------------------------------------------------------------------
# Task 2.2 — POST /checkout/tokens
# ---------------------------------------------------------------------------

def test_checkout_tokens_returns_session_url(
    client, person_uuid, session_token, fake_stripe,
):
    res = client.post(
        '/checkout/tokens',
        json={'sku': 'starter'},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body['url'].startswith('https://checkout.stripe.com/')

    # Stripe was called with mode=payment + the resolved Price.
    call = fake_stripe.checkout.Session.create.call_args
    assert call.kwargs['mode'] == 'payment'
    assert call.kwargs['line_items'][0]['price'] == 'price_test_starter'
    assert call.kwargs['metadata']['sku'] == 'starter'
    assert call.kwargs['client_reference_id'] == person_uuid['uuid']


def test_checkout_tokens_rejects_unknown_sku(
    client, person_uuid, session_token, fake_stripe,
):
    # Pydantic @validate rejects on pattern mismatch before reaching the
    # handler — Pydantic errors surface as 400. If validation is somehow
    # bypassed, the handler's own unknown_sku branch also returns 400.
    res = client.post(
        '/checkout/tokens',
        json={'sku': 'megapack'},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 400


def test_checkout_tokens_unknown_sku_when_env_missing(
    client, person_uuid, session_token, fake_stripe, monkeypatch,
):
    # Valid pattern but the env var is unset → handler returns
    # {"error": "unknown_sku"}, 400. Simulate by clearing one env.
    monkeypatch.delenv('STRIPE_PRICE_TOKENS_PRO', raising=False)
    res = client.post(
        '/checkout/tokens',
        json={'sku': 'pro'},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 400
    assert res.get_json() == {'error': 'unknown_sku'}


def test_checkout_tokens_requires_auth(client, fake_stripe):
    res = client.post('/checkout/tokens', json={'sku': 'single'})
    # Auth-decorator rejects missing bearer with 400 in this codebase
    # (service/api/decorators.py — see test_tokens.py note).
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Task 2.3 — webhook credits tokens on session.mode=payment (idempotent)
# ---------------------------------------------------------------------------

def _make_payment_event(person: dict, sku: str, *,
                        event_id: str | None = None,
                        session_id: str | None = None,
                        amount_total: int = 999):
    """Build a Stripe-shaped checkout.session.completed payload with
    mode=payment, mirroring exactly what /checkout/tokens stamps on the
    session (service/checkout/__init__.py ~250): metadata carries BOTH
    user_id (int person_id, used by _resolve_person_id) and user_uuid;
    client_reference_id carries the person UUID. Without user_id the
    webhook logs "could not resolve person_id" and never credits.
    """
    sid = session_id or f'cs_test_{uuid4().hex}'
    eid = event_id or f'evt_test_{uuid4().hex}'
    return {
        'id':     eid,
        'object': 'event',
        'type':   'checkout.session.completed',
        'data': {
            'object': {
                'object':               'checkout.session',
                'id':                   sid,
                'mode':                 'payment',
                'client_reference_id':  person['uuid'],
                'metadata':             {
                    'sku':       sku,
                    'user_id':   str(person['id']),
                    'user_uuid': person['uuid'],
                },
                'amount_total':         amount_total,
            }
        },
    }


@pytest.fixture
def webhook_env(monkeypatch, fake_stripe):
    """Set the webhook secret so the handler doesn't 503; signature
    verification itself is bypassed because fake_stripe.Webhook is a
    MagicMock that no-ops construct_event."""
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET_CHECKOUT', 'whsec_test')
    return fake_stripe


def _balance(person_uuid_str):
    from database import api_tx
    from service.tokens import get_balance
    with api_tx() as tx:
        return get_balance(tx, person_uuid_str)


def test_webhook_credits_tokens_on_payment_session(
    client, person_uuid, webhook_env,
):
    payload = _make_payment_event(person_uuid, 'starter')
    body = json.dumps(payload).encode()

    res = client.post(
        '/webhooks/stripe-checkout',
        data=body,
        headers={'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'},
    )
    assert res.status_code == 200
    assert _balance(person_uuid['uuid']) == 10  # 'starter' → 10 tokens


def test_webhook_token_credit_is_idempotent(
    client, person_uuid, webhook_env,
):
    # Same session_id, two different event_ids — the outer record_event
    # dedupes by event_id, but the ledger-level check dedupes by
    # session_id. Use distinct event_ids so the second post gets past
    # record_event and is caught only by the ledger guard.
    session_id = f'cs_test_{uuid4().hex}'
    p1 = _make_payment_event(person_uuid, 'plus', session_id=session_id)
    p2 = _make_payment_event(person_uuid, 'plus', session_id=session_id)
    assert p1['id'] != p2['id']

    headers = {'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'}

    r1 = client.post('/webhooks/stripe-checkout',
                     data=json.dumps(p1).encode(), headers=headers)
    assert r1.status_code == 200
    after_first = _balance(person_uuid['uuid'])
    assert after_first == 22  # 'plus' → 22 tokens

    r2 = client.post('/webhooks/stripe-checkout',
                     data=json.dumps(p2).encode(), headers=headers)
    assert r2.status_code == 200
    # Balance MUST NOT change on the replay.
    assert _balance(person_uuid['uuid']) == after_first


# ---------------------------------------------------------------------------
# Phase 8 — subscription stipend (initial + renewal)
# ---------------------------------------------------------------------------
#
# These exercise the new SUBSCRIPTION_TIER_TO_STIPEND credit logic. Two
# layers of idempotency are at play:
#   1. record_event() (outer) dedupes by Stripe event_id.
#   2. _credit_subscription_stipend() (inner) dedupes by session/invoice id
#      stamped into token_ledger.metadata.
# The "initial" path keys on stripe_session_id; the "renewal" path keys on
# stripe_invoice_id.


def _make_subscription_event(person_id: int, person_uuid_str: str, tier: str,
                             *, event_id: str | None = None,
                             session_id: str | None = None,
                             sub_id: str = 'sub_test_xyz'):
    sid = session_id or f'cs_sub_{uuid4().hex}'
    eid = event_id or f'evt_sub_{uuid4().hex}'
    return {
        'id':     eid,
        'object': 'event',
        'type':   'checkout.session.completed',
        'data': {
            'object': {
                'object':              'checkout.session',
                'id':                  sid,
                'mode':                'subscription',
                'client_reference_id': str(person_id),
                'customer':            f'cus_{uuid4().hex}',
                'subscription':        sub_id,
                'metadata': {
                    'user_id':   str(person_id),
                    'tier_key':  tier,
                    'user_uuid': person_uuid_str,
                },
            }
        },
    }


TIER_TO_STIPEND = {'month': 10, 'quart': 12, 'year': 15}


@pytest.mark.parametrize('tier,expected', list(TIER_TO_STIPEND.items()))
def test_webhook_credits_subscription_stipend_on_create(
    client, person_uuid, webhook_env, tier, expected,
):
    # Subscription.retrieve is called on checkout.session.completed to fetch
    # current_period_end — we return a minimal dict so _expiry_from_subscription
    # falls through to the tier default.
    webhook_env.Subscription.retrieve.return_value = SimpleNamespace(
        to_dict_recursive=lambda: {'current_period_end': None, 'metadata': {}},
    )
    payload = _make_subscription_event(
        person_uuid['id'], person_uuid['uuid'], tier,
    )
    res = client.post(
        '/webhooks/stripe-checkout',
        data=json.dumps(payload).encode(),
        headers={'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'},
    )
    assert res.status_code == 200
    assert _balance(person_uuid['uuid']) == expected


def test_webhook_subscription_stipend_idempotent_on_replay(
    client, person_uuid, webhook_env,
):
    """Same session_id, two distinct event_ids — second post must not
    re-credit. Mirrors the token-bundle idempotency test pattern."""
    webhook_env.Subscription.retrieve.return_value = SimpleNamespace(
        to_dict_recursive=lambda: {'current_period_end': None, 'metadata': {}},
    )
    session_id = f'cs_sub_{uuid4().hex}'
    p1 = _make_subscription_event(
        person_uuid['id'], person_uuid['uuid'], 'month',
        session_id=session_id,
    )
    p2 = _make_subscription_event(
        person_uuid['id'], person_uuid['uuid'], 'month',
        session_id=session_id,
    )
    assert p1['id'] != p2['id']
    headers = {'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'}
    r1 = client.post('/webhooks/stripe-checkout',
                     data=json.dumps(p1).encode(), headers=headers)
    assert r1.status_code == 200
    assert _balance(person_uuid['uuid']) == 10
    r2 = client.post('/webhooks/stripe-checkout',
                     data=json.dumps(p2).encode(), headers=headers)
    assert r2.status_code == 200
    assert _balance(person_uuid['uuid']) == 10


def test_webhook_renewal_credits_stipend_on_invoice_payment_succeeded(
    client, person_uuid, webhook_env,
):
    """invoice.payment_succeeded with billing_reason=subscription_cycle
    triggers another stipend credit (renewal). Uses stripe_invoice_id as
    the idempotency key (not session_id), so it does NOT collide with the
    initial-subscription credit's idempotency row."""
    sub_id = 'sub_renew_xyz'
    invoice_id = f'in_test_{uuid4().hex}'
    webhook_env.Subscription.retrieve.return_value = SimpleNamespace(
        to_dict_recursive=lambda: {
            'metadata': {
                'tier_key':    'quart',
                'person_uuid': person_uuid['uuid'],
                'user_uuid':   person_uuid['uuid'],
                'user_id':     str(person_uuid['id']),
            },
        },
    )
    payload = {
        'id':     f'evt_inv_{uuid4().hex}',
        'object': 'event',
        'type':   'invoice.payment_succeeded',
        'data': {
            'object': {
                'object':         'invoice',
                'id':             invoice_id,
                'billing_reason': 'subscription_cycle',
                'subscription':   sub_id,
                'customer':       f'cus_{uuid4().hex}',
                # No metadata on invoice — handler reads tier_key off the
                # subscription via Subscription.retrieve. person_id on the
                # invoice maps via the metadata user_id field below.
                'metadata':       {'user_id': str(person_uuid['id'])},
            }
        },
    }
    res = client.post(
        '/webhooks/stripe-checkout',
        data=json.dumps(payload).encode(),
        headers={'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'},
    )
    assert res.status_code == 200
    assert _balance(person_uuid['uuid']) == 12  # quart → 12


# ---------------------------------------------------------------------------
# Phase 8 — cancellation revokes premium, preserves tokens
# ---------------------------------------------------------------------------

def test_webhook_subscription_cancel_preserves_token_balance(
    client, person_uuid, webhook_env,
):
    """customer.subscription.deleted must (a) revoke the 'premium'
    entitlement + null subscription_expires_at, (b) NOT touch token_ledger.
    Stipend tokens already credited remain spendable after cancellation."""
    from database import api_tx
    from service.tokens import credit

    # Pre-state: user is premium AND has 5 stipend tokens.
    with api_tx() as tx:
        tx.execute(
            """UPDATE person
                  SET entitlements = ARRAY['premium'],
                      subscription_expires_at = NOW() + INTERVAL '30 days'
                WHERE id = %(id)s""",
            dict(id=person_uuid['id']),
        )
        credit(
            tx, person_uuid['uuid'], 5,
            reason='subscription_stipend',
            metadata={'tier_key': 'month', 'pre_cancel': True},
        )

    payload = {
        'id':     f'evt_cancel_{uuid4().hex}',
        'object': 'event',
        'type':   'customer.subscription.deleted',
        'data': {
            'object': {
                'object':   'subscription',
                'id':       'sub_test_cancel',
                'customer': f'cus_{uuid4().hex}',
                'metadata': {
                    'user_id':     str(person_uuid['id']),
                    'person_uuid': person_uuid['uuid'],
                },
            }
        },
    }
    res = client.post(
        '/webhooks/stripe-checkout',
        data=json.dumps(payload).encode(),
        headers={'Stripe-Signature': 't=0,v1=fake', 'Content-Type': 'application/json'},
    )
    assert res.status_code == 200

    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT entitlements, subscription_expires_at FROM person WHERE id = %(id)s',
            dict(id=person_uuid['id']),
        ).fetchone()
    assert 'premium' not in (row['entitlements'] or [])
    assert row['subscription_expires_at'] is None
    # Tokens preserved — this is the key assertion: cancellation must NOT
    # touch token_ledger.
    assert _balance(person_uuid['uuid']) == 5


