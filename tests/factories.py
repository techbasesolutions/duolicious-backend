"""
Test object factories.

Provides `make_user`, `make_thread`, `make_message` per the implementation
plan. Each factory writes a row to the dev DB and returns an attribute-bag
representing that row.

These factories are forward-looking — they return placeholder objects until
Phase 1 lands the country/language schema and Phase 2 lands the message
translations schema. Tests can request the factories via:

    def test_x(db, make_user):
        u = make_user(country='JP', languages_spoken=['en', 'ja'])
        assert u.country == 'JP'

For Phase 0–0.3 tests (which is most of what runs right now), factories are
called only by smoke tests that verify the test harness wiring. Real test
content lands when Phase 1+ tasks add it.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUser:
    """Lightweight stand-in for a `person` row, used by Phase 0 smoke tests.

    Phase 1 Task 0.4 (when the country/language schema lands) will replace
    this with a real DB-backed factory. For now, smoke tests only need the
    factory to return *something* with the expected attribute names.
    """
    id: int = field(default_factory=lambda: secrets.randbelow(2**31))
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    email: str = ''
    name: str = 'Test User'
    is_active: bool = True
    verification_level: str = 'none'
    primary_language: str = 'EN-US'
    languages_spoken: list[str] = field(default_factory=lambda: ['en'])
    country: str | None = None
    region: str | None = None
    auto_translate_enabled: bool = True
    entitlements: list[str] = field(default_factory=list)
    deletion_requested_at: float | None = None


@dataclass
class FakeThread:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    participants: tuple[FakeUser, FakeUser] = field(default_factory=tuple)


@dataclass
class FakeMessage:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = ''
    sender_id: int = 0
    text: str = ''
    detected_source_lang: str | None = None
    translations: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


def make_user(**overrides: Any) -> FakeUser:
    """Returns a FakeUser with sensible defaults; overrides applied verbatim.

    Example:
        u = make_user(country='JP', languages_spoken=['en', 'ja'])
    """
    if 'email' not in overrides and 'name' in overrides:
        overrides.setdefault('email', f"{overrides['name'].lower().replace(' ', '_')}@example.com")
    return FakeUser(**overrides)


def make_thread(a: FakeUser, b: FakeUser, **overrides: Any) -> FakeThread:
    return FakeThread(participants=(a, b), **overrides)


def make_message(thread: FakeThread, sender: FakeUser, text: str = 'hi',
                 **overrides: Any) -> FakeMessage:
    return FakeMessage(
        thread_id=thread.id,
        sender_id=sender.id,
        text=text,
        **overrides,
    )
