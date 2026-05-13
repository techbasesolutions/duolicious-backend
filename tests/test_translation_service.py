"""
Phase 2 Task 2.1 — translation primitive tests.

Run inside the api container:
    docker compose exec api sh -c 'pip install -r tests/requirements-test.txt && python -m pytest tests/test_translation_service.py -v'

Tests use the `redis_mock` (fakeredis) and `deepl_mock` (MagicMock) fixtures
from conftest.py — both auto-substitute into service.translation_service via
monkeypatch when that module exists, which it now does (Phase 2 Task 2.1).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from service.translation_service import (
    TranslationResult,
    cache_key,
    normalize_deepl_target,
    translate,
    translate_messages_for,
)


# ---------------------------------------------------------------------------
# normalize_deepl_target — pure function
# ---------------------------------------------------------------------------

class TestNormalizeDeepLTarget:
    def test_english_gets_us_default(self):
        assert normalize_deepl_target('EN') == 'EN-US'
        assert normalize_deepl_target('en') == 'EN-US'

    def test_portuguese_gets_european_default(self):
        assert normalize_deepl_target('PT') == 'PT-PT'
        assert normalize_deepl_target('pt') == 'PT-PT'

    def test_other_languages_pass_through_uppercased(self):
        assert normalize_deepl_target('JA') == 'JA'
        assert normalize_deepl_target('ja') == 'JA'
        assert normalize_deepl_target('ES') == 'ES'

    def test_explicit_regional_variants_pass_through(self):
        # Caller already specified region; we don't second-guess.
        assert normalize_deepl_target('en-gb') == 'EN-GB'
        assert normalize_deepl_target('PT-BR') == 'PT-BR'


# ---------------------------------------------------------------------------
# cache_key — keyed on (text, target) only
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_same_text_same_target_same_key(self):
        assert cache_key('Hello', 'ES') == cache_key('Hello', 'ES')

    def test_target_case_does_not_affect_key(self):
        # We uppercase target inside cache_key; bare 'es' and 'ES' both hit one entry.
        assert cache_key('Hello', 'ES') == cache_key('Hello', 'es')

    def test_different_text_different_key(self):
        assert cache_key('Hello', 'ES') != cache_key('World', 'ES')

    def test_different_target_different_key(self):
        assert cache_key('Hello', 'ES') != cache_key('Hello', 'JA')


# ---------------------------------------------------------------------------
# translate() — empty / whitespace / no-DeepL pass-through
# ---------------------------------------------------------------------------

class TestTranslatePassthrough:
    def test_empty_text_passes_through(self):
        r = translate('', 'ES')
        assert r == TranslationResult(translated='', detected_source=None, cached=False)

    def test_whitespace_only_passes_through(self):
        r = translate('   \n\t', 'ES')
        assert r.translated == '   \n\t'
        assert r.detected_source is None

    def test_no_deepl_key_passes_through(self, monkeypatch):
        # Reset the lazy-init flag so the test gets a fresh probe.
        import service.translation_service as ts
        monkeypatch.setattr(ts, '_deepl_client', None)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)  # short-circuit re-probe
        # Also disable redis so it doesn't wrap the result
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        r = translate('Hello, how are you?', 'ES')
        assert r.translated == 'Hello, how are you?'
        assert r.detected_source is None


# ---------------------------------------------------------------------------
# translate() — with DeepL mock + Redis cache
# ---------------------------------------------------------------------------

class TestTranslateWithDeepL:
    def test_first_call_invokes_deepl_and_caches(self, monkeypatch):
        import service.translation_service as ts

        # Wire mocks
        deepl_mock = MagicMock(name='deepl_client')
        result_obj = MagicMock()
        result_obj.__str__ = lambda s: 'Hola'
        result_obj.detected_source_lang = 'EN'
        deepl_mock.translate_text.return_value = result_obj

        import fakeredis
        fake = fakeredis.FakeRedis()

        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', fake)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        r1 = translate('Hello', 'ES')
        assert r1.translated == 'Hola'
        assert r1.detected_source == 'EN'
        assert r1.cached is False
        assert deepl_mock.translate_text.call_count == 1

        # Second identical call should hit cache, not DeepL
        r2 = translate('Hello', 'ES')
        assert r2.translated == 'Hola'
        assert r2.cached is True
        assert deepl_mock.translate_text.call_count == 1   # unchanged

    def test_target_normalized_before_deepl_call(self, monkeypatch):
        import service.translation_service as ts

        deepl_mock = MagicMock(name='deepl_client')
        result_obj = MagicMock()
        result_obj.__str__ = lambda s: 'Hello'
        result_obj.detected_source_lang = 'ES'
        deepl_mock.translate_text.return_value = result_obj

        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)   # disable cache for this test
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        translate('Hola', 'EN')   # bare EN → EN-US

        # Confirm DeepL got the normalized target
        call_kwargs = deepl_mock.translate_text.call_args.kwargs
        assert call_kwargs['target_lang'] == 'EN-US'

    def test_deepl_failure_falls_through_silently(self, monkeypatch):
        import service.translation_service as ts

        deepl_mock = MagicMock(name='deepl_client')
        deepl_mock.translate_text.side_effect = Exception('DeepL is down')

        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)

        r = translate('Hello', 'ES')
        # Failure must not raise; fall through to original text.
        assert r.translated == 'Hello'
        assert r.detected_source is None


# ---------------------------------------------------------------------------
# translate_messages_for() — in-place mutation of message list
# ---------------------------------------------------------------------------

class FakeMessage:
    """Minimal stand-in for an ORM message row."""
    def __init__(self, text, detected_source_lang=None, translations=None):
        self.text = text
        self.detected_source_lang = detected_source_lang
        self.translations = translations or {}


class TestTranslateMessagesFor:
    def _wire(self, monkeypatch, translated_text, detected='ES'):
        import service.translation_service as ts
        deepl_mock = MagicMock()
        result_obj = MagicMock()
        result_obj.__str__ = lambda s: translated_text
        result_obj.detected_source_lang = detected
        deepl_mock.translate_text.return_value = result_obj
        monkeypatch.setattr(ts, '_deepl_client', deepl_mock)
        monkeypatch.setattr(ts, '_deepl_init_attempted', True)
        monkeypatch.setattr(ts, '_redis_client', None)
        monkeypatch.setattr(ts, '_redis_init_attempted', True)
        return deepl_mock

    def test_translates_a_foreign_message(self, monkeypatch):
        self._wire(monkeypatch, 'Hello, how are you?', detected='ES')
        m = FakeMessage(text='Hola, ¿cómo estás?')
        translate_messages_for([m], 'EN-US')
        assert m.translations == {'EN-US': 'Hello, how are you?'}
        assert m.detected_source_lang == 'ES'

    def test_skips_when_translation_already_exists(self, monkeypatch):
        deepl = self._wire(monkeypatch, 'Hello', detected='ES')
        m = FakeMessage(
            text='Hola',
            translations={'EN-US': 'Hello (cached previously)'},
        )
        translate_messages_for([m], 'EN-US')
        assert m.translations['EN-US'] == 'Hello (cached previously)'
        assert deepl.translate_text.call_count == 0   # never called

    def test_skips_when_source_matches_target_family(self, monkeypatch):
        deepl = self._wire(monkeypatch, 'Hello', detected='EN')
        m = FakeMessage(text='Hi there', detected_source_lang='EN')
        translate_messages_for([m], 'EN-US')
        # No translation written; same-language detection short-circuits.
        assert m.translations == {}
        assert deepl.translate_text.call_count == 0

    def test_empty_text_messages_ignored(self, monkeypatch):
        self._wire(monkeypatch, 'Hello', detected='ES')
        m = FakeMessage(text='   ')
        translate_messages_for([m], 'EN-US')
        assert m.translations == {}

    def test_target_is_normalized(self, monkeypatch):
        deepl = self._wire(monkeypatch, 'Hello', detected='ES')
        m = FakeMessage(text='Hola')
        translate_messages_for([m], 'EN')   # bare EN → EN-US
        assert 'EN-US' in m.translations
