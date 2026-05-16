"""
Ahavah pytest fixtures.

Provides the named fixtures referenced throughout the implementation plan:
  - `client`       — Flask test client wired to the api package
  - `db`           — wraps `database.api_tx` for tests; rolls back after each test
  - `mock_rekognition` — MagicMock for boto3.client('rekognition') (Phase 4 photo moderation)
  - `signed_rc_event`  — helper to produce a signed RevenueCat webhook payload (Phase 5)
  - `stripe_signed_event` — helper to produce a Stripe webhook payload (Phase 3 ID verify)

  Removed 2026-05-15:
  - `redis_mock` / `deepl_mock` — fakeredis + DeepL fixtures. Translation
    feature pulled (orphan settings page + onboarding promise + service.
    translation_service module all deleted). Restore from git if
    chat-side translation is re-introduced.

These fixtures are forward-looking: most of the modules they mock don't exist
yet (Phase 2/3/4/5 add them). When those modules land, the fixtures auto-wire
via monkeypatch — tests just request the fixture by name.

For now `tests/test_smoke.py` exercises the fixtures end-to-end so we know
the harness itself works before any real code depends on it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

# `service.api` exposes the Flask app via `service.api.decorators.app`.
# Import lazily inside fixtures so a fixture that doesn't need the Flask
# stack doesn't pay the import cost (or fail when the api package isn't on
# the path, e.g. running pytest from outside the container).


# ---------------------------------------------------------------------------
# Flask test client + DB transaction wrapper
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Returns the Flask app instance in test config."""
    from service.api.decorators import app as flask_app
    flask_app.config['TESTING'] = True
    return flask_app


@pytest.fixture
def client(app):
    """Flask test client. Use:
        r = client.post('/health'); assert r.status_code == 200
    """
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    """DB context manager that rolls back after each test.

    Wraps `database.api_tx` such that anything written in the test is
    discarded — tests don't pollute each other's state. Until the migration
    helpers land in Phase 1+, this just yields the underlying api_tx
    function; tests that need state isolation will use a savepoint pattern
    until then.
    """
    from database import api_tx
    yield api_tx


# ---------------------------------------------------------------------------
# External-service mocks
# ---------------------------------------------------------------------------

# `redis_mock` + `deepl_mock` fixtures removed 2026-05-15 along with
# the service.translation_service module. They were the only call sites
# for fakeredis / DeepL in the test suite. Restore from git history if
# translation gets re-introduced.


@pytest.fixture
def mock_rekognition(monkeypatch):
    """MagicMock that stands in for the photo-moderation classifier.

    Audit correction (Task 0.0): Phase 4 Task 4.0 extends the existing ONNX
    `antiabuse.antiporn.predict_nsfw` instead of wiring AWS Rekognition,
    so this fixture monkeypatches `service.photo_moderation._predict_nsfw`
    (the lazy hook). The fixture name stays `mock_rekognition` for plan
    fidelity; tests using it should configure the mock's return value via
    `mock.return_value = [score_float, ...]` or by setting it as a callable.

    Test usage example:
        mock_rekognition.return_value = [0.95]   # rejected
    """
    mock = MagicMock(name='photo_moderation_classifier')
    # By default, return an empty list — tests must override.
    mock.return_value = []
    try:
        import service.photo_moderation as pm  # type: ignore
        # The mock IS the predict_nsfw callable for the test's duration.
        monkeypatch.setattr(pm, '_predict_nsfw', mock)
    except ImportError:
        pass
    return mock


# ---------------------------------------------------------------------------
# Webhook payload helpers (Phase 3 + Phase 5)
# ---------------------------------------------------------------------------

@dataclass
class SignedEvent:
    body: bytes
    sig: str


@pytest.fixture
def stripe_signed_event() -> Callable[..., SignedEvent]:
    """Produces a Stripe webhook payload + a signature header that
    `stripe.Webhook.construct_event` will accept under the test secret.
    Used by Phase 3's identity-verification tests.

    Test expectation (from plan Task 3.1 Step 2):
        payload = stripe_signed_event(
            type='identity.verification_session.verified',
            metadata={'user_id': str(user.id)},
            verified_outputs={'document': {'issuing_country': 'US'}},
        )
        client.post('/webhooks/stripe-identity',
                    data=payload.body, headers={'Stripe-Signature': payload.sig})
    """
    import hmac, hashlib, json, time

    def _build(type: str = 'identity.verification_session.verified',
               metadata: dict | None = None,
               verified_outputs: dict | None = None,
               secret: str = 'whsec_test') -> SignedEvent:
        # The top-level `"object": "event"` and inner
        # `"object": "identity.verification_session"` discriminators are
        # required by stripe SDK 11+ — `Webhook.construct_event` parses them
        # to instantiate the correct StripeObject subclass and raises an
        # AttributeError on `__getattr__('object')` if missing.
        payload = {
            'id':      f'evt_test_{int(time.time() * 1000)}',
            'object':  'event',
            'type':    type,
            'data': {
                'object': {
                    'object':           'identity.verification_session',
                    'metadata':         metadata or {},
                    'verified_outputs': verified_outputs or {},
                }
            },
        }
        body = json.dumps(payload).encode('utf-8')
        ts = str(int(time.time()))
        signed = f'{ts}.{body.decode()}'
        sig = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return SignedEvent(body=body, sig=f't={ts},v1={sig}')

    return _build


@pytest.fixture
def signed_rc_event() -> Callable[..., SignedEvent]:
    """Produces a RevenueCat webhook payload + bearer-auth header value.
    Used by Phase 5's IAP receipt-validation tests.

    Test expectation (from plan Task 5.2 Step 2):
        payload = signed_rc_event(
            type='INITIAL_PURCHASE',
            app_user_id=str(user.id),
            entitlement_id='premium',
            expires_at='2027-05-08T00:00:00Z',
        )
        client.post('/webhooks/revenuecat', data=payload.body,
                    headers={'Authorization': f'Bearer {RC_AUTH}'})
    """
    import json, time

    def _build(type: str = 'INITIAL_PURCHASE',
               app_user_id: str = '1',
               entitlement_id: str = 'premium',
               event_id: str | None = None,
               expires_at: str = '2099-01-01T00:00:00Z') -> SignedEvent:
        payload = {
            'event': {
                'id': event_id or f'rc_evt_{int(time.time() * 1000)}',
                'type': type,
                'app_user_id': app_user_id,
                'entitlement_id': entitlement_id,
                'expiration_at_ms': 0,
                'expiration_iso': expires_at,
            }
        }
        body = json.dumps(payload).encode('utf-8')
        # RC uses bearer-token auth, not signed payloads. The "sig" we return
        # is the bearer header value; tests pass it via Authorization.
        return SignedEvent(body=body, sig='Bearer test-rc-secret')

    return _build
