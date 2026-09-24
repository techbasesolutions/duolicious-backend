"""communityweekly - sends e2, the weekly community email, on a schedule.

Until 2026-09-24 nothing sent this at all. It shipped with a template, a
send script and a Send button in Growth > Emails, and no scheduler, so it
went out exactly once, by hand, months after it was built.

THE CAMPAIGN ID IS THE GUARD, NOT THE CLOCK.

Every tick inside the window calls run_campaign with the week's id from
`service.campaigns.weekid`. The runner's `_Q_SAME_RUN` rejects a recipient
who already has a row for that (campaign, campaign_id), so:

  * a second tick in the same Monday queues nothing,
  * a container restart mid-Monday queues nothing extra,
  * an operator who presses Send in Growth > Emails the same week collides
    with the identical id and queues nothing extra either.

That last one is why the id lives in a shared module rather than being a
literal here: the admin frontend computes the same string.

The 6-day cap (`CAP_DAYS` in emails/send_community_weekly.py) stays on. It
is a second, independent belt: the id guard protects against a repeat of
THIS run, the cap protects the member from any other campaign that went
out to them in the last six days.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from database import api_tx
from emails.community_weekly import FROM_ADDR
from emails.send_community_weekly import (
    CAP_DAYS, UNSUB_SCOPE, build_for, recipients)
from service.campaigns.runner import run_campaign
from service.campaigns.weekid import week_campaign_id
from service.config import WEB_BASE_URL
from service.cron.cronutil import env_int, print_stacktrace, MAX_RANDOM_START_DELAY
from service.unsubscribe import make_url as unsub_url

# Monday 12:00 UTC is 08:00 in Barbados, the same slot the member of the
# week cards are scheduled into (`mondaySlots` in the admin frontend).
SEND_WEEKDAY = env_int('DUO_CRON_COMMUNITY_WEEKLY_WEEKDAY', 1)  # 1 = Monday
SEND_HOUR_UTC = env_int('DUO_CRON_COMMUNITY_WEEKLY_HOUR_UTC', 12)

# Hourly. The window is the rest of the day and the id guard absorbs every
# repeat, so the poll only has to be fine enough to catch the day.
COMMUNITY_WEEKLY_POLL_SECONDS = env_int(
    'DUO_CRON_COMMUNITY_WEEKLY_POLL_SECONDS', 60 * 60)

# Off by default so deploying this does not start sending mail on its own.
# The owner turns it on once, deliberately.
COMMUNITY_WEEKLY_ENABLED = env_int('DUO_CRON_COMMUNITY_WEEKLY_ENABLED', 0)

print(f'Hello from cron module: {__name__}')


def is_send_window(now: datetime) -> bool:
    """Monday, at or after the send hour. A window rather than an instant:
    a poll never wakes exactly on the hour, and a restart can skip one."""
    return now.isoweekday() == SEND_WEEKDAY and now.hour >= SEND_HOUR_UTC


def _send_once() -> None:
    now = datetime.now(timezone.utc)
    if not is_send_window(now):
        return
    cid = week_campaign_id(now, 'e2')
    out = run_campaign(
        api_tx, 'e2', cid, recipients(), build_for, send=True,
        from_addr=FROM_ADDR, unsub_scope=UNSUB_SCOPE, cap_days=CAP_DAYS,
        list_unsubscribe=lambda e: (
            f"<mailto:support@ahavah.app?subject=Unsubscribe>, "
            f"<{unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))
    # A tick that queued nothing is the NORMAL case: every hour of Monday
    # after the first one. Only say something when something happened.
    if out['queued'] or out['error']:
        print(f'community_weekly: {out}')


async def community_weekly_forever() -> None:
    if not COMMUNITY_WEEKLY_ENABLED:
        print('community_weekly: disabled, set DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1')
        return
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_send_once))
        await asyncio.sleep(COMMUNITY_WEEKLY_POLL_SECONDS)
