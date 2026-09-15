"""The single drain of the spotlight cleanup job table (Wave 2 F09 part 2).

Everything else enqueues: the retention sweep, the removal-done route, a
withdrawal's cancelled rows, a superseded upload, a caption edit that
re-points a rendered request. This loop is the only place an object is
actually deleted from storage, so deletion happens in exactly one process, on
a schedule, with retries and a visible abandoned state instead of inline
inside whichever request or sweep decided the artwork was finished with.

`run_cleanup_batch` is a plain sync function (it opens short transactions in
sequence and makes a blocking storage call between them), so it runs on a
thread here and the cron event loop is never blocked. Same shape as
`service/cron/spotlightretention` and `service/cron/emailoutbox`.

Two alert lines the operator needs and nothing else prints: the outstanding
backlog while the emergency stop is engaged, and removal tasks past their
deadline. The overdue count is read on every tick, stop or no stop -- a
removal order nobody actioned is exactly the thing a stop must not hide.
"""
from __future__ import annotations

import asyncio
import os
import random

from database import api_tx
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY, env_int
from service.spotlight.cleanup import overdue_removals, run_cleanup_batch

SPOTLIGHT_CLEANUP_POLL_SECONDS = env_int('DUO_CRON_SPOTLIGHT_CLEANUP_POLL_SECONDS', 600)

print(f'Hello from cron module: {__name__}')


def _cleanup_once() -> None:
    out = run_cleanup_batch(api_tx)
    if out['halted']:
        print(f"spotlight_cleanup: halted, {out['outstanding']} job(s) outstanding")
    elif out['reserved']:
        print(f'spotlight_cleanup: {out}')
    with api_tx('read committed') as tx:
        overdue = overdue_removals(tx)
    if overdue:
        print(f'spotlight_cleanup: {overdue} removal task(s) overdue')


async def spotlight_cleanup_forever() -> None:
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_cleanup_once))
        await asyncio.sleep(SPOTLIGHT_CLEANUP_POLL_SECONDS)
