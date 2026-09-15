"""Spotlight card retention sweep (spec 3.6): once a published card's row is
older than RETENTION_DAYS, the rendered image no longer needs to live in the
object store. This does not touch the row's status or the platform post
itself -- only the stored artwork.

`retention_sweep` is a plain sync function taking a sync `tx` (mirrors
`service.spotlight.queue`, which is used the same way from the Flask routes'
`with api_tx() as tx:` blocks). The forever loop below runs it on a thread so
the cron event loop is never blocked on the DB round trip, following the
pattern `service/cron/betareengagement` uses for its sync SMTP call.
"""
from __future__ import annotations

import asyncio
import os
import random

from database import api_tx
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY
from service.spotlight.storage import delete_images

RETENTION_DAYS = 90

SPOTLIGHT_RETENTION_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_SPOTLIGHT_RETENTION_POLL_SECONDS',
    str(60 * 60 * 24),  # 24 hours
))

print(f'Hello from cron module: {__name__}')

_Q_SWEEP = """
    SELECT id, image_key FROM publishing_queue
     WHERE status = 'published'
       AND updated_at < NOW() - make_interval(days => %(days)s)
       AND image_key IS NOT NULL
"""


def retention_sweep(tx) -> int:
    rows = tx.execute(_Q_SWEEP, dict(days=RETENTION_DAYS)).fetchall()
    if not rows:
        return 0
    keys = [r['image_key'] for r in rows]
    # Task 6 moves this onto the cleanup job; for now only the confirmed
    # count is logged, the same as delete_images was always best-effort.
    confirmed = delete_images(keys)
    print(f'spotlight_retention: confirmed {len(confirmed)}/{len(keys)} image(s) deleted from storage')
    tx.execute(
        """UPDATE publishing_queue SET image_key = NULL, image_url = NULL, image_sha256 = NULL
            WHERE id = ANY(%(ids)s::uuid[])""",
        dict(ids=[str(r['id']) for r in rows]))
    return len(rows)


def _retention_sweep_once():
    with api_tx() as tx:
        n = retention_sweep(tx)
    if n:
        print(f'spotlight_retention: swept {n} row(s)')


async def spotlight_retention_forever() -> None:
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_retention_sweep_once))
        await asyncio.sleep(SPOTLIGHT_RETENTION_POLL_SECONDS)
