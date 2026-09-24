"""Migration 0053: the verification job gets a lease and its own outcome.

Three columns, all additive:

  `running_since`          when the current run started, which is the lease
                           the reaper measures against.
  `reap_count`             how many times this row has been re-queued, which
                           is what stops a dying job looping forever.
  `verification_level_id`  the outcome of THIS attempt, so a per-job answer
                           is never read off the per-person latch.

The backfill is the part worth pinning. A row that was already 'running'
when this ran has no start time, and treating an unknown age as stale would
double-process whatever the old code still had in flight during the deploy.
Stamping those rows with NOW() gives each of them exactly one full lease
before it can be reaped.
"""
from pathlib import Path

import psycopg

import database
from database import api_tx

_MIGRATION = (Path(__file__).resolve().parent.parent
              / 'migrations' / '0053_verification_job_lease.sql')

# Any two transactions that pick this number up serialise against each
# other. Arbitrary, and only has to be unique among this suite's advisory
# locks; 53 is the migration.
_MIGRATION_ADVISORY_LOCK = 53


def _ddl_connection():
    """A connection for running the migration file on, set up so it cannot
    stall the rest of the suite.

    `ALTER TABLE` takes ACCESS EXCLUSIVE on `verification_job` and holds it
    until this transaction ends, so everything else that touches that table
    queues behind it, and anything already holding a lock on it blocks the
    ALTER. Serially, which is how the suite runs today, that is harmless.
    Under `-n auto` it is a stall in both directions.

    Two guards. The advisory lock serialises the copies of this test
    against each other, so two workers cannot each be waiting for the lock
    the other is about to take. `lock_timeout` means a wait on any other
    holder fails in a second, loudly and with a lock name, instead of
    sitting there until the connection's 5 second statement_timeout kills
    it with something less legible. Between them the ACCESS EXCLUSIVE
    window is two ALTER statements and a SELECT.
    """
    conn = psycopg.connect(database._api_conninfo)
    try:
        conn.execute("SET lock_timeout = '1s'")
        conn.execute('SELECT pg_advisory_xact_lock(%s)',
                     (_MIGRATION_ADVISORY_LOCK,))
    except BaseException:
        conn.close()
        raise
    return conn


def test_0053_columns_exist_with_the_declared_shape():
    with api_tx('read committed') as tx:
        cols = {r['column_name']: r for r in tx.execute(
            """SELECT column_name, data_type, is_nullable, column_default
                 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'verification_job'
                  AND column_name IN (
                      'running_since', 'reap_count', 'verification_level_id')"""
        ).fetchall()}
    assert set(cols) == {'running_since', 'reap_count', 'verification_level_id'}, \
        'migration 0053 has not been applied'
    assert cols['running_since']['data_type'] == 'timestamp with time zone'
    assert cols['running_since']['is_nullable'] == 'YES'
    assert cols['reap_count']['data_type'] == 'integer'
    assert cols['reap_count']['is_nullable'] == 'NO'
    assert cols['reap_count']['column_default'] == '0'
    assert cols['verification_level_id']['data_type'] == 'smallint'
    assert cols['verification_level_id']['is_nullable'] == 'YES'


def test_0053_level_id_points_at_the_verification_level_lookup():
    """The job's outcome is the same vocabulary as the person's, not a
    second spelling of it."""
    with api_tx('read committed') as tx:
        row = tx.execute(
            """SELECT ccu.table_name AS referenced
                 FROM information_schema.table_constraints AS tc
                 JOIN information_schema.key_column_usage AS kcu
                   ON kcu.constraint_name = tc.constraint_name
                 JOIN information_schema.constraint_column_usage AS ccu
                   ON ccu.constraint_name = tc.constraint_name
                WHERE tc.table_name = 'verification_job'
                  AND tc.constraint_type = 'FOREIGN KEY'
                  AND kcu.column_name = 'verification_level_id'"""
        ).fetchone()
    assert row and row['referenced'] == 'verification_level'


def test_0053_applies_twice_and_leaves_existing_rows_alone(make_person):
    """Re-running the file must not restamp a lease that is already set, or
    the reaper's clock resets on every deployment."""
    person = make_person()
    with _ddl_connection() as conn:
        try:
            job_id = conn.execute(
                """INSERT INTO verification_job (
                       person_id, status, message, photo_uuid, running_since)
                   VALUES (%s, 'running', '', 'mig-0053-proof',
                           NOW() - INTERVAL '10 minutes')
                   RETURNING id""",
                (person['id'],),
            ).fetchone()[0]
            before = conn.execute(
                'SELECT running_since FROM verification_job WHERE id = %s',
                (job_id,)).fetchone()[0]

            conn.execute(_MIGRATION.read_text())
            conn.execute(_MIGRATION.read_text())

            after = conn.execute(
                """SELECT running_since, reap_count, verification_level_id
                     FROM verification_job WHERE id = %s""",
                (job_id,)).fetchone()
        finally:
            conn.rollback()

    assert after[0] == before, 'an existing lease was restamped'
    assert after[1] == 0
    assert after[2] is None


def test_0053_backfills_a_running_row_that_has_no_lease(make_person):
    """The deploy window case: old code set 'running' with no start time.
    The migration gives it one so it is reaped once, not immediately."""
    person = make_person()
    with _ddl_connection() as conn:
        try:
            job_id = conn.execute(
                """INSERT INTO verification_job (
                       person_id, status, message, photo_uuid, running_since)
                   VALUES (%s, 'running', '', 'mig-0053-backfill', NULL)
                   RETURNING id""",
                (person['id'],),
            ).fetchone()[0]

            conn.execute(_MIGRATION.read_text())

            after = conn.execute(
                'SELECT running_since FROM verification_job WHERE id = %s',
                (job_id,)).fetchone()[0]
        finally:
            conn.rollback()

    assert after is not None, 'a running row was left with no lease'
