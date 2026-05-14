"""
pendingdeletion — hard-deletes person rows whose 7-day grace expired.

Soft-delete flow (Phase W cutover, migration 0008):
  1. User taps Delete on /settings/account
  2. Backend sets person.activated = FALSE + deletion_requested_at = NOW().
     User vanishes from /search, /matches, /profile/[uuid] immediately.
  3. This cron scans for rows with deletion_requested_at < NOW() - 7 days
     and hard-deletes them (cascades remove messages, photos, swipes).

Cancellation: a user emails admin@ahavah.app within 7 days; admin
flips activated=true + clears deletion_requested_at. A self-service
cancel-deletion endpoint can land later.
"""

from database.asyncdatabase import api_tx
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY
import asyncio
import os
import random


PENDING_DELETION_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_PENDING_DELETION_POLL_SECONDS',
    str(60 * 60),  # 1 hour
))

GRACE_PERIOD_DAYS = int(os.environ.get(
    'DUO_PENDING_DELETION_GRACE_DAYS',
    '7',
))

print(f'Hello from cron module: {__name__}')


async def hard_delete_expired_once():
    """Find pending-delete person rows past their grace window and hard-
    delete them. Postgres FK ON DELETE CASCADE rules in init-api.sql
    cleanly remove the user's photos, swipes, matches, and messages."""
    async with api_tx() as tx:
        cur = await tx.execute(
            """
            DELETE FROM person
             WHERE deletion_requested_at IS NOT NULL
               AND deletion_requested_at < NOW() - (%(days)s || ' days')::INTERVAL
            RETURNING id
            """,
            dict(days=GRACE_PERIOD_DAYS),
        )
        rows = await cur.fetchall()

    if rows:
        print(f'Hard-deleted {len(rows)} pending-deletion person row(s)')


async def hard_delete_expired_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(hard_delete_expired_once)
        await asyncio.sleep(PENDING_DELETION_POLL_SECONDS)
