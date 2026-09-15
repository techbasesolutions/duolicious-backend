"""
pendingdeletion: hard-deletes person rows whose 7-day grace expired.

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
from database import api_tx as _sync_api_tx
from service.cron.cronutil import env_int, print_stacktrace, MAX_RANDOM_START_DELAY
from service.spotlight.withdrawal import withdraw_member
import asyncio
import random


PENDING_DELETION_POLL_SECONDS = env_int(
    'DUO_CRON_PENDING_DELETION_POLL_SECONDS',
    60 * 60,  # 1 hour
)

GRACE_PERIOD_DAYS = env_int('DUO_PENDING_DELETION_GRACE_DAYS', 7)

print(f'Hello from cron module: {__name__}')

_Q_EXPIRED = """
    SELECT id, uuid::TEXT AS uuid, email
      FROM person
     WHERE deletion_requested_at IS NOT NULL
       AND deletion_requested_at < NOW() - (%(days)s || ' days')::INTERVAL
       AND (sign_in_time IS NULL OR sign_in_time < deletion_requested_at)
"""


def _withdraw_all(person_ids: list) -> list:
    """Withdraws Spotlight for every person about to be hard-deleted, on a
    single sync transaction/connection (not the caller's async one).

    Deliberately called from `hard_delete_expired_once` BEFORE that
    function's own (async) transaction opens, rather than from inside it:
    this database defaults to REPEATABLE READ, so an async transaction that
    was already open when this sync transaction commits a write against the
    same `person` row (the consent-epoch bump every withdrawal makes) would
    hit a write-write conflict the moment it later tried to DELETE that row
    itself (Postgres refuses to serialize it, regardless of statement
    order) -- retrying would not help, since the same overlap would recur
    every attempt. Keeping the two transactions from ever being open at the
    same time avoids the conflict entirely. Otherwise follows the same
    threaded-sync-from-async-cron pattern
    `service.cron.spotlightretention.retention_sweep` already uses.

    Returns the ids actually withdrawn (fix round 1): the caller's second
    (hard-delete) pass intersects its own fresh row set against this list,
    so a person who was not part of THIS withdrawal pass can never be
    hard-deleted in the same run -- including someone who only crosses the
    grace boundary during the pass -- and simply waits for the next hourly
    run, where a withdrawal pass will cover them first."""
    if not person_ids:
        return []
    withdrawn = []
    with _sync_api_tx() as tx:
        for person_id in person_ids:
            withdraw_member(tx, person_id, 'hard_delete')
            withdrawn.append(person_id)
    return withdrawn


async def hard_delete_expired_once():
    """Find pending-delete person rows past their grace window and hard-
    delete them. Postgres FK ON DELETE CASCADE rules in init-api.sql
    cleanly remove the user's photos, swipes, matches, and `mam_message`
    rows where the deleter was author/owner. Tables WITHOUT a cascading
    FK (audit Privacy #1, #2) are cleaned manually inside the same tx:

      - `inbox`      : XMPP message-receipt store keyed on `luser` JID-name
                       (= person.uuid). Stores last-message bodies + peer
                       JIDs; orphan rows survived hard-delete previously.
      - `waitlist_signup`: pre-signup demographic row keyed on email.
                       Survived hard-delete because no FK / no cleanup.

    F11: photo/audio rows themselves DO cascade off `person_id`, but the
    CDN cleaners never look at `photo`/`audio` directly; they only poll
    `undeleted_photo`/`undeleted_audio`. So the uuids must be read and
    staged into those queues BEFORE the person delete runs (the cascade
    would otherwise remove the rows out from under us).

    F12 (migration 0037 regression): `referral.inviter_email` used to
    REFERENCE beta_signup(email) ON DELETE CASCADE; 0037 dropped that FK
    so any member can be an inviter, and `invitee_email` never had one.
    Neither direction cascades off `person` anymore, so both are purged
    here by email.

    Wave 1 F03: Spotlight is withdrawn for every candidate row in its own
    pass, BEFORE the transaction below (the one that actually hard-deletes)
    even opens -- see `_withdraw_all`'s docstring for why the two must not
    overlap in time. Fix round 1: the second pass only hard-deletes rows
    `_withdraw_all` actually withdrew, so anyone who crosses the grace
    boundary during the withdrawal pass (not selected by it, so never
    withdrawn) is excluded from this run rather than hard-deleted without
    ever having Spotlight touched -- they are picked up correctly next hour.
    """
    async with api_tx() as tx:
        candidates = await (await tx.execute(_Q_EXPIRED, dict(days=GRACE_PERIOD_DAYS))).fetchall()

    withdrawn = set(await asyncio.to_thread(_withdraw_all, [r['id'] for r in candidates])) if candidates else set()

    async with api_tx() as tx:
        # Re-selected rather than trusting the candidates list above: the
        # withdrawal pass takes a moment, and re-querying means this
        # transaction only ever acts on rows that are STILL expired right
        # now (e.g. one could in principle have been un-deleted meanwhile).
        # Intersected against `withdrawn` so nothing not covered by the
        # withdrawal pass above is ever hard-deleted this run.
        expired_cur = await tx.execute(_Q_EXPIRED, dict(days=GRACE_PERIOD_DAYS))
        rows = [r for r in await expired_cur.fetchall() if r['id'] in withdrawn]

        if rows:
            person_ids = [r['id'] for r in rows]
            uuids = [r['uuid'] for r in rows]
            emails = [r['email'] for r in rows if r.get('email')]

            # F11: stage photo/audio uuids for the CDN cleaners BEFORE the
            # cascading person delete removes the rows we'd read them from.
            await tx.execute(
                """
                INSERT INTO undeleted_photo (uuid)
                SELECT uuid FROM photo WHERE person_id = ANY(%(ids)s)
                ON CONFLICT DO NOTHING
                """,
                dict(ids=person_ids),
            )
            await tx.execute(
                """
                INSERT INTO undeleted_audio (uuid)
                SELECT uuid FROM audio WHERE person_id = ANY(%(ids)s)
                ON CONFLICT DO NOTHING
                """,
                dict(ids=person_ids),
            )

            # F12 (0037 regression): referral emails have no FK anymore;
            # purge both directions so deleted members' addresses do not
            # persist and UNIQUE(invitee_email) cannot block re-signup.
            if emails:
                await tx.execute(
                    """
                    DELETE FROM referral
                     WHERE inviter_email = ANY(%(emails)s)
                        OR invitee_email = ANY(%(emails)s)
                    """,
                    dict(emails=emails),
                )

            await tx.execute(
                "DELETE FROM person WHERE id = ANY(%(ids)s)",
                dict(ids=person_ids),
            )

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
