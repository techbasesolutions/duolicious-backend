"""
pendingdeletion — hard-deletes person rows whose 7-day grace expired.

Soft-delete flow (Phase W cutover, migration 0008):
  1. User taps Delete on /settings/account
  2. Backend sets person.activated = FALSE + deletion_requested_at = NOW().
     User vanishes from /search, /matches, /profile/[uuid] immediately.
  3. This cron scans for rows with deletion_requested_at < NOW() - 7 days
     and hard-deletes them (cascades remove messages, photos, swipes).

Cancellation: a user emails support@ahavah.app within 7 days; admin
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
    cleanly remove the user's photos, swipes, matches, and `mam_message`
    rows where the deleter was author/owner. Tables WITHOUT a cascading
    FK (audit Privacy #1, #2) are cleaned manually inside the same tx:

      - `inbox`      — XMPP message-receipt store keyed on `luser` JID-name
                       (= person.uuid). Stores last-message bodies + peer
                       JIDs; orphan rows survived hard-delete previously.
      - `waitlist_signup` — pre-signup demographic row keyed on email.
                       Survived hard-delete because no FK / no cleanup.
    """
    async with api_tx() as tx:
        cur = await tx.execute(
            """
            DELETE FROM person
             WHERE deletion_requested_at IS NOT NULL
               AND deletion_requested_at < NOW() - (%(days)s || ' days')::INTERVAL
            RETURNING id, uuid::TEXT AS uuid, email
            """,
            dict(days=GRACE_PERIOD_DAYS),
        )
        rows = await cur.fetchall()

        if rows:
            uuids = [r['uuid'] for r in rows]
            emails = [r['email'] for r in rows if r.get('email')]

            # Audit Privacy #1: inbox has no FK + no cascade; clean here.
            # Schema (init-api.sql:1639): luser = local JID name; we match
            # on it. Use ANY for the batch.
            await tx.execute(
                "DELETE FROM inbox WHERE luser = ANY(%(uuids)s)",
                dict(uuids=uuids),
            )

            # Audit Privacy #2: waitlist_signup has no FK; clean here.
            if emails:
                await tx.execute(
                    "DELETE FROM waitlist_signup WHERE email = ANY(%(emails)s)",
                    dict(emails=emails),
                )
                # And the beta cohort row, same rationale. beta_signup
                # gained an FK in migration 0022, so this is belt-and-
                # braces in case the cascade somehow misses on email
                # equality (e.g. if person_id was NULL at delete time).
                await tx.execute(
                    "DELETE FROM beta_signup WHERE email = ANY(%(emails)s)",
                    dict(emails=emails),
                )

    if rows:
        print(f'Hard-deleted {len(rows)} pending-deletion person row(s) + inbox/waitlist cleanup')


async def hard_delete_expired_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(hard_delete_expired_once)
        await asyncio.sleep(PENDING_DELETION_POLL_SECONDS)
