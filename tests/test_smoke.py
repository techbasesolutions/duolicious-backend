"""
Test-harness smoke tests.

These exist to verify the test infrastructure itself works. They do NOT test
product behavior. Real tests for scam_detection / photo_moderation /
entitlements land in their respective tasks (Phase 4-5).

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_smoke.py -v'
"""

from __future__ import annotations

import pytest

from tests.factories import make_user, make_thread, make_message


def test_health_endpoint_returns_ok(client):
    """The Flask test client is wired and the api package imports cleanly."""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.data == b'status: ok'


def test_make_user_factory_produces_overridable_users():
    a = make_user()
    b = make_user(name='Alice', country='JP', languages_spoken=['en', 'ja'])

    assert a.id != b.id
    assert b.name == 'Alice'
    assert b.country == 'JP'
    assert b.languages_spoken == ['en', 'ja']
    assert b.email == 'alice@example.com'   # auto-derived from name


def test_make_thread_links_two_users():
    alice = make_user(name='Alice')
    bob = make_user(name='Bob')
    t = make_thread(alice, bob)
    assert t.participants == (alice, bob)


def test_make_message_carries_text_and_thread():
    a, b = make_user(name='A'), make_user(name='B')
    t = make_thread(a, b)
    m = make_message(t, a, text='hello')

    assert m.text == 'hello'
    assert m.thread_id == t.id
    assert m.sender_id == a.id
    assert m.translations == {}
    assert m.detected_source_lang is None


# test_redis_mock_fixture_provides_fakeredis + test_deepl_mock_fixture_provides_magicmock
# removed 2026-05-15 — the underlying redis_mock + deepl_mock fixtures
# were translation-specific; service.translation_service is deleted.


def test_mock_rekognition_fixture_provides_magicmock(mock_rekognition):
    # Audit correction: Phase 4 Task 4.0 uses the on-device ONNX classifier,
    # not AWS Rekognition. The fixture stands in for `predict_nsfw` and is
    # called with a list of BytesIO buffers, returning a list of floats.
    mock_rekognition.return_value = [0.95]
    scores = mock_rekognition([object()])
    assert scores == [0.95]


def test_stripe_signed_event_fixture_returns_body_and_sig(stripe_signed_event):
    payload = stripe_signed_event(
        type='identity.verification_session.verified',
        metadata={'user_id': '42'},
    )
    assert payload.body.startswith(b'{')
    assert payload.sig.startswith('t=')
    assert ',v1=' in payload.sig


def test_signed_rc_event_fixture_returns_body_and_bearer(signed_rc_event):
    payload = signed_rc_event(
        type='INITIAL_PURCHASE',
        app_user_id='42',
        entitlement_id='premium',
    )
    assert payload.body.startswith(b'{')
    assert payload.sig.startswith('Bearer ')
