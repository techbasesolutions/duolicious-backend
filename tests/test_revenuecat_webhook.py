"""
Phase 5 Task 5.2 — RevenueCat webhook + entitlements tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_revenuecat_webhook.py -v'

We test the handler function directly via flask test_request_context (auth
is the bearer header, no app session needed). Entitlements DB layer is
mocked since migration 0005 may not yet be applied — same pattern as the
identity_verification tests.

For the full DB-backed integration, a follow-up `test_entitlements_db.py`
will run once the migration is applied.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RC_SECRET = 'test-rc-secret'


def _patch_env(monkeypatch, secret=RC_SECRET):
    monkeypatch.setenv('RC_WEBHOOK_AUTH', secret)


def _patch_no_env(monkeypatch):
    monkeypatch.delenv('RC_WEBHOOK_AUTH', raising=False)


def _patch_entitlements(monkeypatch, *, record_returns=True, grant_returns=True, revoke_returns=True):
    """Stub the DB layer so we can test the webhook handler in isolation."""
    import service.revenuecat_webhook as rcw

    calls = {'record': [], 'grant': [], 'revoke': []}

    def fake_record(event_id, event_type, app_user_id, payload):
        calls['record'].append((event_id, event_type, app_user_id, payload))
        return record_returns

    def fake_grant(person_id, name, expires_at=None):
        calls['grant'].append((person_id, name, expires_at))
        return grant_returns

    def fake_revoke(person_id, name):
        calls['revoke'].append((person_id, name))
        return revoke_returns

    monkeypatch.setattr(rcw, 'record_event', fake_record)
    monkeypatch.setattr(rcw, 'grant', fake_grant)
    monkeypatch.setattr(rcw, 'revoke', fake_revoke)
    return calls


# ---------------------------------------------------------------------------
# Auth + config
# ---------------------------------------------------------------------------

class TestAuth:
    def test_unconfigured_returns_503(self, app, monkeypatch):
        _patch_no_env(monkeypatch)
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context('/webhooks/revenuecat', method='POST', data=b'{}'):
            r = post_revenuecat_webhook()
            _, code = r
            assert code == 503

    def test_missing_authorization_header_401(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        _patch_entitlements(monkeypatch)
        payload = signed_rc_event(type='INITIAL_PURCHASE', app_user_id='1')
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST', data=payload.body, headers={},
        ):
            r = post_revenuecat_webhook()
            _, code = r
            assert code == 401

    def test_wrong_secret_401(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        _patch_entitlements(monkeypatch)
        payload = signed_rc_event(type='INITIAL_PURCHASE', app_user_id='1')
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': 'Bearer wrong'},
        ):
            r = post_revenuecat_webhook()
            _, code = r
            assert code == 401

    def test_correct_secret_passes(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='INITIAL_PURCHASE', app_user_id='42',
            entitlement_id='premium', expires_at='2027-05-08T00:00:00Z',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert isinstance(r, dict)
            assert r['received'] is True


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_invalid_json_returns_400(self, app, monkeypatch):
        _patch_env(monkeypatch)
        _patch_entitlements(monkeypatch)
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=b'not-json{{{',
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            _, code = r
            assert code == 400

    def test_missing_event_id_returns_400(self, app, monkeypatch):
        _patch_env(monkeypatch)
        _patch_entitlements(monkeypatch)
        bad = json.dumps({'event': {'type': 'INITIAL_PURCHASE', 'app_user_id': '1'}}).encode()
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=bad, headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            _, code = r
            assert code == 400

    def test_missing_app_user_id_acks_but_does_not_apply(
        self, app, monkeypatch, signed_rc_event,
    ):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='INITIAL_PURCHASE', app_user_id='', entitlement_id='premium',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['received'] is True
            assert r['applied'] is False
            assert r['reason'] == 'no_user_id'
            # ledger insert still happened — financial event stays auditable
            assert len(calls['record']) == 1
            assert calls['grant'] == []


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------

class TestEventDispatch:
    def test_initial_purchase_grants_with_expiry(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='INITIAL_PURCHASE',
            app_user_id='42',
            entitlement_id='premium',
            expires_at='2027-05-08T00:00:00Z',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['action'] == 'grant'
            assert r['applied'] is True

        assert len(calls['grant']) == 1
        person_id, name, expires = calls['grant'][0]
        assert person_id == 42
        assert name == 'premium'
        assert expires is not None
        assert expires.year == 2027 and expires.month == 5

    def test_renewal_grants(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='RENEWAL', app_user_id='42', entitlement_id='premium',
            expires_at='2028-01-01T00:00:00Z',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['action'] == 'grant'
        assert calls['grant'][0][1] == 'premium'

    def test_expiration_revokes(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='EXPIRATION', app_user_id='42', entitlement_id='premium',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['action'] == 'revoke'

        assert calls['revoke'] == [(42, 'premium')]
        assert calls['grant'] == []

    def test_cancellation_is_ledger_only_keeps_access(self, app, monkeypatch, signed_rc_event):
        # Cancellation just means "won't auto-renew." Access stays until EXPIRATION.
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='CANCELLATION', app_user_id='42', entitlement_id='premium',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['action'] == 'ledger_only'
        assert calls['grant'] == []
        assert calls['revoke'] == []

    def test_billing_issue_is_ledger_only(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='BILLING_ISSUE', app_user_id='42', entitlement_id='premium',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['action'] == 'ledger_only'
        assert calls['grant'] == []
        assert calls['revoke'] == []

    def test_unknown_event_type_is_acked_no_grant_no_revoke(
        self, app, monkeypatch, signed_rc_event,
    ):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        payload = signed_rc_event(
            type='SOMETHING_NEW_RC_INVENTED', app_user_id='42',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r = post_revenuecat_webhook()
            assert r['received'] is True
            assert r['action'] == 'unknown_ledger_only'
        assert calls['grant'] == [] and calls['revoke'] == []


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

class TestReplayProtection:
    def test_replay_does_not_re_grant(self, app, monkeypatch, signed_rc_event):
        _patch_env(monkeypatch)
        calls = _patch_entitlements(monkeypatch)
        # F2: replay is now detected by a read-only pre-check BEFORE the
        # effect runs, and the latch (record_event) commits LAST, after
        # the effect. Simulate persistence with a local "seen" set instead
        # of relying on record_event's return value for the early-return
        # decision (that's what the real ledger row does).
        import service.revenuecat_webhook as rcw

        seen: set[str] = set()

        def fake_already_processed(event_id):
            return event_id in seen

        def fake_record(event_id, event_type, app_user_id, payload):
            calls['record'].append((event_id, event_type, app_user_id, payload))
            seen.add(event_id)
            return True

        monkeypatch.setattr(rcw, '_already_processed', fake_already_processed)
        monkeypatch.setattr(rcw, 'record_event', fake_record)

        payload = signed_rc_event(
            type='INITIAL_PURCHASE', app_user_id='42', entitlement_id='premium',
            event_id='rc_evt_replay_42',
        )
        from service.revenuecat_webhook import post_revenuecat_webhook

        # First delivery — grants
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r1 = post_revenuecat_webhook()
            assert r1['applied'] is True

        # Second delivery (same event_id) — replay
        with app.test_request_context(
            '/webhooks/revenuecat', method='POST',
            data=payload.body,
            headers={'Authorization': f'Bearer {RC_SECRET}'},
        ):
            r2 = post_revenuecat_webhook()
            assert r2['replay'] is True
            assert r2['applied'] is False

        # grant called exactly once across two deliveries
        assert len(calls['grant']) == 1
