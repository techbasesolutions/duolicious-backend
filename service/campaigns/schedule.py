"""When the weekly community email is due, and whether anything will send it.

This lives in a leaf module because two very different callers need the same
few settings: the cron that does the sending, and the Growth > Emails route
that has to tell an operator when the next one goes out. Importing the cron
package from an API route would pull the runner, the templates and the
database helpers in behind it for the sake of three integers. Same reasoning,
and the same shape, as `service/verificationlease.py`.

THE SCHEDULE IS A WINDOW, NOT AN INSTANT.

A poll never wakes exactly on the hour and a container restart can skip an
instant entirely, so the cron sends at any point on Monday from the send hour
onward. What stops a second send is not the clock but the week's campaign id
(`service.campaigns.weekid`) meeting the runner's per-run dedupe.

Monday 12:00 UTC is 08:00 in Barbados, the same slot the member of the week
cards are scheduled into.

It is OFF by default. A deploy that starts mailing the whole membership on
its own is not something anybody asked for, so the owner turns it on once,
deliberately, with DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


def _env_int(name: str, default: int) -> int:
    """Unset or blank reads as the default. Compose files pass every cron
    setting through as an empty string when it is not configured."""
    raw = os.environ.get(name, '')
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


SEND_WEEKDAY = _env_int('DUO_CRON_COMMUNITY_WEEKLY_WEEKDAY', 1)  # 1 = Monday
SEND_HOUR_UTC = _env_int('DUO_CRON_COMMUNITY_WEEKLY_HOUR_UTC', 12)
COMMUNITY_WEEKLY_ENABLED = bool(
    _env_int('DUO_CRON_COMMUNITY_WEEKLY_ENABLED', 0))


def is_send_window(now: datetime) -> bool:
    """The send day, at or after the send hour."""
    return now.isoweekday() == SEND_WEEKDAY and now.hour >= SEND_HOUR_UTC


def next_send_at(now: datetime) -> datetime:
    """The next slot strictly after `now`.

    Inside the window this returns NEXT week's slot, not this one, because
    inside the window the send has either already happened or is about to on
    the current tick. An operator reading "next send in 4 hours" while the
    email is going out right now would be worse than useless.
    """
    slot = now.replace(hour=SEND_HOUR_UTC, minute=0, second=0, microsecond=0)
    ahead = (SEND_WEEKDAY - slot.isoweekday()) % 7
    slot += timedelta(days=ahead)
    if slot <= now:
        slot += timedelta(days=7)
    return slot.astimezone(timezone.utc)
