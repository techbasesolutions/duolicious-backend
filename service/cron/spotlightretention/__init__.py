"""Spotlight card retention sweep (spec 3.6): once a published card's row is
older than RETENTION_DAYS, the rendered image no longer needs to live in the
object store. This does not touch the row's status or the platform post
itself -- only the stored artwork.

Wave 2 Task 5 (F09): the sweep no longer deletes anything and no longer
clears a key. It enqueues one `asset_delete` cleanup job per key and stops
there; `service.spotlight.cleanup.run_cleanup_batch` is what deletes the
object and, only once storage has CONFIRMED the deletion, clears the row's
image columns. A key that is still set is therefore the truth ("the object
may still be there"), which is what makes the sweep safe to re-run: enqueueing
is idempotent on `(kind, target)`, and a row drops out of the sweep the moment
its job confirms.

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
from service.spotlight.cleanup import enqueue_asset_delete

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
    """Enqueue one cleanup job per retained image key. Returns the number of
    rows swept, not the number of jobs newly created: a row whose job is
    already queued from an earlier tick is still a row this sweep is waiting
    on, and it stops being counted the moment the job confirms and the key is
    cleared. Deletes nothing and clears nothing -- that is the cleanup batch's
    job, and only against a confirmed deletion."""
    rows = tx.execute(_Q_SWEEP, dict(days=RETENTION_DAYS)).fetchall()
    for r in rows:
        enqueue_asset_delete(tx, r['image_key'])
    return len(rows)


def _retention_sweep_once():
    with api_tx() as tx:
        n = retention_sweep(tx)
    if n:
        print(f'spotlight_retention: {n} row(s) queued for asset cleanup')


async def spotlight_retention_forever() -> None:
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_retention_sweep_once))
        await asyncio.sleep(SPOTLIGHT_RETENTION_POLL_SECONDS)
