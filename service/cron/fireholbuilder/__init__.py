"""fireholbuilder — the single writer of the FireHOL binary blocklist.

Downloads the netsets and rewrites the mmap file the api workers read
(see antiabuse/firehol). Runs here, in the cron container, so the
transient download/parse cost never lands on the api container — the
whole reason the old per-worker design had to be disabled.

The api fails open while the file is absent (first boot on a fresh
volume), so a build failure degrades to "no IP-reputation layer",
never to blocked sign-ins.
"""
import asyncio
import os
import random

from antiabuse.firehol import build_blocklist_file
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY

FIREHOLBUILDER_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_FIREHOLBUILDER_POLL_SECONDS',
    str(60 * 60 * 4),  # 4 hours, matching the lists' own refresh cadence
))

print(f'Hello from cron module: {__name__}')


async def build_once():
    counts = await asyncio.to_thread(build_blocklist_file)
    print(f'fireholbuilder: build complete {counts}', flush=True)


async def build_firehol_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(build_once)
        await asyncio.sleep(FIREHOLBUILDER_POLL_SECONDS)
