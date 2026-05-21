"""Chat translation (2026-05-20) - service.translation.translate tests.

Mocks the OpenAI client + Redis; no live calls.
"""
from __future__ import annotations

import service.translation as tr


def test_passthrough_when_key_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(tr, "_redis", lambda: None)
    out = tr.translate("hola", "en-US")
    assert out["translated"] == "hola"
    assert out["cached"] is False


def test_whitespace_passthrough(monkeypatch):
    monkeypatch.setattr(tr, "_redis", lambda: None)
    out = tr.translate("   ", "en-US")
    assert out["translated"] == "   "


def test_cache_hit(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.store = {}

        def get(self, k):
            return self.store.get(k)

        def setex(self, k, ttl, v):
            self.store[k] = v

    fake = FakeRedis()
    monkeypatch.setattr(tr, "_redis", lambda: fake)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = {"n": 0}

    def fake_call(text, target, model):
        calls["n"] += 1
        return "hello"

    monkeypatch.setattr(tr, "_openai_translate", fake_call)
    first = tr.translate("hola", "en-US")
    assert first["translated"] == "hello" and first["cached"] is False
    second = tr.translate("hola", "en-US")
    assert second["translated"] == "hello" and second["cached"] is True
    assert calls["n"] == 1  # second served from cache


def test_openai_failure_passthrough(monkeypatch):
    monkeypatch.setattr(tr, "_redis", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(text, target, model):
        raise RuntimeError("api down")

    monkeypatch.setattr(tr, "_openai_translate", boom)
    out = tr.translate("hola", "en-US")
    assert out["translated"] == "hola"  # never errors the chat


def test_too_long_raises():
    import pytest
    with pytest.raises(ValueError):
        tr.translate("x" * (tr.MAX_CHARS + 1), "en-US")
