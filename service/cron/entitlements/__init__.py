"""
entitlements cron — hourly sweep stripping expired premium.

F10: expire_stale (service/entitlements/__init__.py) existed with ZERO
callers, so subscription_expires_at was decorative and every grant was
premium-forever. This cron is the single enforcement point. NOTE the
member-visible consequence: the day the founding cohort's dates pass
(Dec 2026 - Feb 2027), premium genuinely ends for them; owner comms are
tracked outside this repo.

No DRY_RUN here (unlike autodeactivate2 / photocleaner / audiocleaner):
stripping expired entitlements IS the entire point of this cron, and the
operation is idempotent (re-running against an already-stripped row is a
no-op, per expire_stale's `entitlements <> '{}'` guard) so there is no
irreversible side effect to gate behind a flag.

expire_stale() itself is synchronous (it uses `database.api_tx`, not the
async `database.asyncdatabase.api_tx` the rest of this package's cron
modules use) because it's shared, non-cron code called directly by
service/entitlements callers. It's called directly (not awaited) from
this async loop; the DB call briefly blocks the event loop, same
trade-off other crons accept for their own blocking I/O.
"""
import asyncio
import os
import random
from datetime import datetime, timezone

from service.cron.cronutil import MAX_RANDOM_START_DELAY, print_stacktrace
from service.entitlements import expire_stale

ENTITLEMENTS_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_ENTITLEMENTS_POLL_SECONDS',
    str(60 * 60),  # 1 hour
))

print(f'Hello from cron module: {__name__}')


async def entitlements_once():
    n = expire_stale(datetime.now(timezone.utc))
    if n:
        print(f'entitlements: stripped {n} expired premium grant(s)', flush=True)


async def entitlements_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(entitlements_once)
        await asyncio.sleep(ENTITLEMENTS_POLL_SECONDS)
