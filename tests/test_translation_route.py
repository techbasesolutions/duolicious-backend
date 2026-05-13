"""
Phase 2 Task 2.4 — POST /translate-preview handler tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_translation_route.py -v'

We test the handler function directly (with a flask test_request_context to
provide `request.get_json`) rather than going through the HTTP layer, because
the HTTP layer's auth decorator requires a real session-token row in
Postgres — out of scope for this primitive's tests.

A separate end-to-end test that does provision a real session token belongs
in tests/test_e2e.py once Phase 6 lands.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from service.translation import post_translate_preview, PREVIEW_MAX_CHARS


def _session(person_id=1):
    """Bare-minimum SessionInfo lookalike — handler only reads .person_id."""
    return SimpleNamespace(person_id=person_id, email='u@example.com')


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_unauthenticated_returns_401(self, app):
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hi', 'target': 'ES'}):
            r = post_translate_preview(SimpleNamespace(person_id=None))
            assert r == ('Not authorized', 401)

    def test_missing_text_returns_400(self, app):
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'target': 'ES'}):
            r = post_translate_preview(_session())
            body, code = r
            assert code == 400
            assert 'text' in body

    def test_missing_target_returns_400(self, app):
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hello'}):
            r = post_translate_preview(_session())
            body, code = r
            assert code == 400
            assert 'target' in body

    def test_blank_target_returns_400(self, app):
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hello', 'target': '   '}):
            r = post_translate_preview(_session())
            body, code = r
            assert code == 400

    def test_text_over_cap_returns_413(self, app):
        too_long = 'x' * (PREVIEW_MAX_CHARS + 1)
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': too_long, 'target': 'ES'}):
            r = post_translate_preview(_session())
            _, code = r
            assert code == 413

    def test_text_at_cap_is_accepted(self, app, monkeypatch):
        # Disable DeepL + Redis so it just passes through.
        import service.translation_service as ts
        monkeypatch.setattr(ts, '_deepl_client', None)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        at_cap = 'x' * PREVIEW_MAX_CHARS
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': at_cap, 'target': 'ES'}):
            r = post_translate_preview(_session())
            assert r['translated'] == at_cap   # passthrough


# ---------------------------------------------------------------------------
# Behavior — passthrough mode (no DEEPL_API_KEY)
# ---------------------------------------------------------------------------

class TestPassthroughMode:
    def test_returns_original_text_when_deepl_unavailable(self, app, monkeypatch):
        import service.translation_service as ts
        monkeypatch.setattr(ts, '_deepl_client', None)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hello', 'target': 'ES'}):
            r = post_translate_preview(_session())
            assert r == {
                'translated':      'Hello',
                'detected_source': None,
                'cached':          False,
                'target':          'ES',
            }


# ---------------------------------------------------------------------------
# Behavior — with DeepL mock
# ---------------------------------------------------------------------------

class TestWithDeepL:
    def _wire(self, monkeypatch, translated_text, detected='ES'):
        import service.translation_service as ts
        deepl_mock = MagicMock(name='deepl_client')
        result_obj = MagicMock()
        result_obj.__str__ = lambda s: translated_text
        result_obj.detected_source_lang = detected
        deepl_mock.translate_text.return_value = result_obj
        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)   # no cache
        monkeypatch.setattr(ts, '_redis_init_attempted', True)
        return deepl_mock

    def test_translates_text(self, app, monkeypatch):
        self._wire(monkeypatch, 'Hola', detected='EN')
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hello', 'target': 'ES'}):
            r = post_translate_preview(_session())
            assert r['translated'] == 'Hola'
            assert r['detected_source'] == 'EN'
            assert r['target'] == 'ES'
            assert r['cached'] is False

    def test_target_normalized_in_response(self, app, monkeypatch):
        # bare 'EN' → 'EN-US'; bare 'PT' → 'PT-PT'
        deepl = self._wire(monkeypatch, 'Hello', detected='ES')
        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hola', 'target': 'EN'}):
            r = post_translate_preview(_session())
            assert r['target'] == 'EN-US'
            assert deepl.translate_text.call_args.kwargs['target_lang'] == 'EN-US'

    def test_deepl_failure_falls_through(self, app, monkeypatch):
        import service.translation_service as ts
        deepl_mock = MagicMock(name='deepl_client')
        deepl_mock.translate_text.side_effect = Exception('boom')
        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        with app.test_request_context('/translate-preview', method='POST',
                                      json={'text': 'Hello', 'target': 'ES'}):
            r = post_translate_preview(_session())
            # Failure must not raise; client gets original text back.
            assert r['translated'] == 'Hello'
            assert r['detected_source'] is None
