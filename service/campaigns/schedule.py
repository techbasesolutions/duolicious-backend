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


_TRUE = frozenset({'1', 'true', 'yes', 'on'})
_FALSE = frozenset({'', '0', 'false', 'no', 'off'})


def _env_flag(name: str) -> bool:
    """The on switch, spelled any of the obvious ways.

    An operator turning this on will reach for `true` or `yes` as readily as
    `1`, and a flag that reads an unrecognised value as off fails in the
    worst possible direction: the log line is identical to a correct dormant
    deploy, so you believe you enabled a thing you did not. Anything we do
    not recognise raises at import instead, which stops the container and is
    impossible to miss.
    """
    raw = os.environ.get(name, '').strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a yes or a no. Use one of "
        f"{sorted(_TRUE)} or {sorted(_FALSE)}.")


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    """Unset or blank reads as the default, because compose files pass every
    setting through as an empty string when it is not configured.

    Out of range raises rather than clamping. `SEND_HOUR_UTC=25` would
    otherwise reach `datetime.replace(hour=25)` inside the Growth > Emails
    route and take the whole tab down with a 500, and `SEND_WEEKDAY=0` would
    give a window that can never open beside a next-send date the dashboard
    prints forever.
    """
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name}={raw!r} is not a whole number.") from None
    if not low <= value <= high:
        raise ValueError(f"{name}={value} is outside {low} to {high}.")
    return value


# ISO weekday: 1 is Monday, 7 is Sunday. isoweekday() never returns 0, so 0
# here would be a window that can never open.
SEND_WEEKDAY = _env_int('DUO_CRON_COMMUNITY_WEEKLY_WEEKDAY', 1, low=1, high=7)
SEND_HOUR_UTC = _env_int('DUO_CRON_COMMUNITY_WEEKLY_HOUR_UTC', 12, low=0, high=23)
COMMUNITY_WEEKLY_ENABLED = _env_flag('DUO_CRON_COMMUNITY_WEEKLY_ENABLED')


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
