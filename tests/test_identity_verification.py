"""
Phase 3 Task 3.1 — identity_verification module tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_identity_verification.py -v'

Tests:
  - promote_user() — pure rank/idempotency logic (no DB; api_tx mocked)
  - post_stripe_identity_webhook() — signature verification + event dispatch
  - post_start_id_flow() — auth + Stripe API call dispatch (mocked)

We don't hit the real `person` table because migration 0003 may not yet be
applied in this environment. The DB layer is mocked via a fake api_tx
context manager; this is consistent with how Phase 2 tests mocked the
DeepL/Redis lazy clients.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(person_id=42):
    return SimpleNamespace(person_id=person_id, email='u@example.com')


class FakeRow(dict):
    """psycopg's Row supports dict-style access; this stand-in does too."""


@contextmanager
def _fake_tx_factory(rows_per_query):
    """Builds a context-manager factory that yields a tx whose execute()
    returns rows from `rows_per_query` in order. Each entry is the row that
    the next .fetchone() should return.

    Used to mock `database.api_tx()` from inside identity_verification.
    """
    queries: list[str] = []
    rows_iter = iter(rows_per_query)

    class FakeTx:
        def execute(self, sql, params=None):
            queries.append((sql, params))
            self._next = next(rows_iter, None)
            return self

        def fetchone(self):
            return self._next

    yield FakeTx, queries


# ---------------------------------------------------------------------------
# promote_user — pure logic
# ---------------------------------------------------------------------------

class TestPromoteUser:
    def _patch_api_tx(self, monkeypatch, rows):
        """Patches `service.identity_verification.api_tx` to return a fake
        context manager whose tx returns `rows` in order from fetchone()."""
        import service.identity_verification as iv

        queries: list = []
        rows_iter = iter(rows)

        class FakeTx:
            def execute(self, sql, params=None):
                queries.append((sql, params))
                self._next = next(rows_iter, None)
                return self
            def fetchone(self):
                return self._next

        @contextmanager
        def fake_api_tx(*args, **kwargs):
            yield FakeTx()

        monkeypatch.setattr(iv, 'api_tx', fake_api_tx)
        return queries

    def test_invalid_level_raises(self, monkeypatch):
        from service.identity_verification import promote_user
        with pytest.raises(ValueError):
            promote_user(1, 'platinum')

    def test_person_not_found_returns_false(self, monkeypatch):
        self._patch_api_tx(monkeypatch, rows=[None])
        from service.identity_verification import promote_user
        assert promote_user(1, 'gold') is False

    def test_already_at_same_level_returns_false(self, monkeypatch):
        self._patch_api_tx(monkeypatch, rows=[FakeRow(ahavah_verification_tier='gold')])
        from service.identity_verification import promote_user
        assert promote_user(1, 'gold') is False

    def test_already_at_higher_level_blocks_demotion(self, monkeypatch):
        # User is gold; a stale silver event must NOT demote them.
        self._patch_api_tx(monkeypatch, rows=[FakeRow(ahavah_verification_tier='gold')])
        from service.identity_verification import promote_user
        assert promote_user(1, 'silver') is False

    def test_promotes_none_to_gold_with_country(self, monkeypatch):
        queries = self._patch_api_tx(
            monkeypatch, rows=[FakeRow(ahavah_verification_tier='none')]
        )
        from service.identity_verification import promote_user
        assert promote_user(42, 'gold', country='US') is True

        # We should see two queries: the SELECT + the UPDATE.
        assert len(queries) == 2
        update_sql, update_params = queries[1]
        assert 'ahavah_verification_tier' in update_sql
        assert 'id_verified_country' in update_sql
        assert update_params['country'] == 'US'
        assert update_params['id'] == 42

    def test_promotes_silver_to_gold(self, monkeypatch):
        queries = self._patch_api_tx(
            monkeypatch, rows=[FakeRow(ahavah_verification_tier='silver')]
        )
        from service.identity_verification import promote_user
        assert promote_user(7, 'gold', country='GB') is True
        assert queries[1][1]['country'] == 'GB'

    def test_promotes_none_to_bronze_no_country_column(self, monkeypatch):
        queries = self._patch_api_tx(
            monkeypatch, rows=[FakeRow(ahavah_verification_tier='none')]
        )
        from service.identity_verification import promote_user
        assert promote_user(7, 'bronze') is True
        # bronze writes only ahavah_verification_tier, no country
        assert 'id_verified_country' not in queries[1][0]


# ---------------------------------------------------------------------------
# Webhook — signature + dispatch
# ---------------------------------------------------------------------------

class TestStripeIdentityWebhook:
    """We use the real `stripe.Webhook.construct_event` against the test
    signing secret. The conftest `stripe_signed_event` fixture builds
    payloads under that same secret."""

    SECRET = 'whsec_test'

    def _wire_stripe(self, monkeypatch):
        """Bypass lazy-init: pre-populate the stripe module + secret."""
        import stripe
        import service.identity_verification as iv
        monkeypatch.setattr(iv, '_stripe_module', stripe)
        monkeypatch.setattr(iv, '_stripe_init_attempted', True)
        monkeypatch.setattr(iv, '_webhook_secret', self.SECRET)

    def _no_stripe(self, monkeypatch):
        """Webhook is not configured — should 503."""
        import service.identity_verification as iv
        monkeypatch.setattr(iv, '_stripe_module', None)
        monkeypatch.setattr(iv, '_stripe_init_attempted', True)
        monkeypatch.setattr(iv, '_webhook_secret', None)

    def test_unconfigured_returns_503(self, app, monkeypatch):
        self._no_stripe(monkeypatch)
        from service.identity_verification import post_stripe_identity_webhook
        with app.test_request_context('/webhooks/stripe-identity', method='POST',
                                      data=b'{}', headers={}):
            r = post_stripe_identity_webhook()
            _, code = r
            assert code == 503

    def test_invalid_signature_returns_400(self, app, monkeypatch, stripe_signed_event):
        self._wire_stripe(monkeypatch)
        from service.identity_verification import post_stripe_identity_webhook
        payload = stripe_signed_event(secret='whsec_wrong')
        with app.test_request_context('/webhooks/stripe-identity', method='POST',
                                      data=payload.body,
                                      headers={'Stripe-Signature': payload.sig}):
            r = post_stripe_identity_webhook()
            _, code = r
            assert code == 400

    def test_verified_event_calls_promote_user_with_country(
        self, app, monkeypatch, stripe_signed_event,
    ):
        self._wire_stripe(monkeypatch)

        promote_calls = []

        def fake_promote(person_id, level, country=None):
            promote_calls.append((person_id, level, country))
            return True

        import service.identity_verification as iv
        monkeypatch.setattr(iv, 'promote_user', fake_promote)

        payload = stripe_signed_event(
            type='identity.verification_session.verified',
            metadata={'user_id': '99'},
            verified_outputs={'document': {'issuing_country': 'US'}},
            secret=self.SECRET,
        )
        with app.test_request_context('/webhooks/stripe-identity', method='POST',
                                      data=payload.body,
                                      headers={'Stripe-Signature': payload.sig}):
            r = iv.post_stripe_identity_webhook()
            assert r == {'received': True, 'promoted': True}
            assert promote_calls == [(99, 'gold', 'US')]

    def test_verified_event_without_user_id_acks_but_does_not_promote(
        self, app, monkeypatch, stripe_signed_event,
    ):
        self._wire_stripe(monkeypatch)

        promote_called = []
        import service.identity_verification as iv
        monkeypatch.setattr(
            iv, 'promote_user',
            lambda *a, **k: (promote_called.append((a, k)) or True),
        )

        payload = stripe_signed_event(
            type='identity.verification_session.verified',
            metadata={},   # no user_id
            verified_outputs={'document': {'issuing_country': 'US'}},
            secret=self.SECRET,
        )
        with app.test_request_context('/webhooks/stripe-identity', method='POST',
                                      data=payload.body,
                                      headers={'Stripe-Signature': payload.sig}):
            r = iv.post_stripe_identity_webhook()
            assert r['received'] is True
            assert r['promoted'] is False
            assert promote_called == []   # never called

    def test_canceled_event_acked_no_promote(
        self, app, monkeypatch, stripe_signed_event,
    ):
        self._wire_stripe(monkeypatch)

        promote_called = []
        import service.identity_verification as iv
        monkeypatch.setattr(
            iv, 'promote_user',
            lambda *a, **k: (promote_called.append((a, k)) or True),
        )

        payload = stripe_signed_event(
            type='identity.verification_session.canceled',
            metadata={'user_id': '99'},
            secret=self.SECRET,
        )
        with app.test_request_context('/webhooks/stripe-identity', method='POST',
                                      data=payload.body,
                                      headers={'Stripe-Signature': payload.sig}):
            r = iv.post_stripe_identity_webhook()
            assert r == {'received': True, 'promoted': False}
            assert promote_called == []


# ---------------------------------------------------------------------------
# /verification/start-id-flow
# ---------------------------------------------------------------------------

class TestStartIdFlow:
    def test_unauthenticated_returns_401(self, app):
        from service.identity_verification import post_start_id_flow
        with app.test_request_context('/verification/start-id-flow', method='POST'):
            r = post_start_id_flow(SimpleNamespace(person_id=None))
            _, code = r
            assert code == 401

    def test_unconfigured_returns_503(self, app, monkeypatch):
        import service.identity_verification as iv
        monkeypatch.setattr(iv, '_stripe_module', None)
        monkeypatch.setattr(iv, '_stripe_init_attempted', True)

        with app.test_request_context('/verification/start-id-flow', method='POST'):
            r = iv.post_start_id_flow(_session())
            _, code = r
            assert code == 503

    def test_creates_session_returns_client_secret(self, app, monkeypatch):
        import service.identity_verification as iv

        fake_session = MagicMock()
        fake_session.id = 'vs_abc'
        fake_session.client_secret = 'vs_abc_secret_xyz'
        fake_session.url = 'https://verify.stripe.com/vs_abc'

        fake_stripe = MagicMock()
        fake_stripe.identity.VerificationSession.create.return_value = fake_session

        monkeypatch.setattr(iv, '_stripe_module', fake_stripe)
        monkeypatch.setattr(iv, '_stripe_init_attempted', True)

        with app.test_request_context('/verification/start-id-flow', method='POST'):
            r = iv.post_start_id_flow(_session(person_id=42))

        assert r['session_id'] == 'vs_abc'
        assert r['client_secret'] == 'vs_abc_secret_xyz'
        assert r['url'] == 'https://verify.stripe.com/vs_abc'

        # Confirm metadata.user_id is wired correctly
        call_kwargs = fake_stripe.identity.VerificationSession.create.call_args.kwargs
        assert call_kwargs['metadata'] == {'user_id': '42'}
        assert call_kwargs['type'] == 'document'

    def test_stripe_failure_returns_502(self, app, monkeypatch):
        import service.identity_verification as iv
        fake_stripe = MagicMock()
        fake_stripe.identity.VerificationSession.create.side_effect = Exception('Stripe down')
        monkeypatch.setattr(iv, '_stripe_module', fake_stripe)
        monkeypatch.setattr(iv, '_stripe_init_attempted', True)

        with app.test_request_context('/verification/start-id-flow', method='POST'):
            r = iv.post_start_id_flow(_session())
            _, code = r
            assert code == 502
