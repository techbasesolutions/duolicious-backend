"""Message-email fallback rules (2026-07-29).

The notifications cron is the only path that tells an offline member
about unread messages when web push doesn't reach them. Web push is
fire-and-forget: a stale subscription endpoint is indistinguishable
from a delivered push, and the caller burns the notification
watermark either way. So a live push_subscription row must NOT skip
the email; only the member's own email_messages preference may.
"""
from __future__ import annotations

import asyncio

import service.cron.notifications as n


def _row(**overrides):
    defaults = dict(
        person_uuid='00000000-0000-0000-0000-000000000001',
        last_intro_notification_seconds=0,
        last_chat_notification_seconds=0,
        last_intro_seconds=0,
        last_chat_seconds=2_000_000_000,
        has_intro=False,
        has_chat=True,
        name='Test',
        email='member@example.org',
        chats_drift_seconds=0,
        intros_drift_seconds=0,
        has_live_push=False,
        push_messages=True,
        email_messages=True,
    )
    defaults.update(overrides)
    return n.PersonNotification(**defaults)


def _sent(monkeypatch, row) -> bool:
    called = []

    async def fake_send(r):
        called.append(r)

    monkeypatch.setattr(n, 'send_email_notification', fake_send)
    asyncio.run(n.send_notification(row))
    return bool(called)


def test_live_push_subscription_does_not_skip_email(monkeypatch):
    """A push_subscription row proves nothing about delivery. If the
    member is still offline with unread messages when the cron fires,
    they get the email."""
    assert _sent(monkeypatch, _row(has_live_push=True, push_messages=True))


def test_member_email_preference_still_respected(monkeypatch):
    """email_messages=False is the member's explicit choice and must
    keep suppressing the fallback."""
    assert not _sent(monkeypatch, _row(email_messages=False))
