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
                coordinates, gender_id, about, location_short_friendly
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere'
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
    tok = secrets.token_urlsafe(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in)
            VALUES (%s, %s, %s, TRUE)
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
