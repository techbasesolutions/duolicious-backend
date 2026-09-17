"""Migration 0050: at most one live welcome per member and platform.

Wave 3d Task 3 (acceptance Runtime 8a): eight concurrent welcome calls for
one member created four request keys and four E4 invites, because the
route's own duplicate guard is a read that every racing worker passes
together. The partial unique index is that guard in database form.

The migration also refuses, with a message naming each conflict, to build
the index over rows that already break it. That path is exercised against a
temporary table named `publishing_queue`: a temp table shadows the real one
for its own session (pg_temp is searched first), so the migration's own SQL
runs unmodified without touching or locking the shared table. The file
carries its own BEGIN/COMMIT (and its LOCK TABLE lands on the shadow), so it
runs on an autocommit connection, the way psql runs it.
"""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

import database
from database import api_tx
from service.spotlight.queue import create_candidate

_MIGRATION = Path(__file__).resolve().parent.parent / 'migrations' / '0050_spotlight_welcome_unique.sql'


def _make_eligible(make_person, name='Elig0050', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (gen_random_uuid(), %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""",
                   dict(id=p['id']))
    return p


def test_0050_index_exists_with_the_declared_shape():
    with api_tx('read committed') as tx:
        row = tx.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'publishing_queue_one_live_welcome'").fetchone()
    assert row is not None, 'migration 0050 has not been applied'
    definition = row['indexdef']
    assert definition.startswith('CREATE UNIQUE INDEX')
    assert '(subject_person_id, platform)' in definition
    assert "kind = 'welcome'::text" in definition and "status <> 'cancelled'::text" in definition


def test_0050_refuses_a_second_live_welcome_but_not_after_a_cancel(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    with api_tx() as tx:
        tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE subject_person_id = %(p)s",
                   dict(p=p['id']))
        create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    with api_tx('read committed') as tx:
        live = tx.execute(
            """SELECT count(*) AS n FROM publishing_queue
                WHERE subject_person_id = %(p)s AND kind = 'welcome' AND status <> 'cancelled'""",
            dict(p=p['id'])).fetchone()['n']
    assert live == 2   # one request, one row per platform


def _shadow(conn, rows):
    conn.execute("""CREATE TEMP TABLE publishing_queue (
                        subject_person_id int, platform text NOT NULL,
                        kind text NOT NULL, status text NOT NULL)""")
    for r in rows:
        conn.execute("INSERT INTO publishing_queue VALUES (%(p)s, %(pl)s, %(k)s, %(s)s)", r)


def _end(conn):
    """Close out whatever transaction the migration file left open (a refused
    run leaves its own BEGIN aborted). The temp table dies with the session."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.execute('ROLLBACK')


def test_0050_pre_check_names_the_conflict_instead_of_failing_opaquely():
    person = 900000000 + uuid4().int % 1000000
    with psycopg.connect(database._api_conninfo, autocommit=True) as conn:
        try:
            _shadow(conn, [dict(p=person, pl='facebook', k='welcome', s='review'),
                           dict(p=person, pl='facebook', k='welcome', s='awaiting_member')])
            with pytest.raises(psycopg.errors.RaiseException) as raised:
                conn.execute(_MIGRATION.read_text())
        finally:
            _end(conn)
    message = str(raised.value)
    assert 'more than one live welcome' in message
    assert f'person {person} on facebook has 2' in message


def test_0050_pre_check_ignores_cancelled_rows_and_members_since_deleted():
    """A deleted member's rows keep `subject_person_id` NULL (ON DELETE SET
    NULL). A unique index treats NULLs as distinct, so those rows can never
    block the index, and the pre-check must not refuse over them either."""
    with psycopg.connect(database._api_conninfo, autocommit=True) as conn:
        try:
            _shadow(conn, [dict(p=None, pl='facebook', k='welcome', s='published'),
                           dict(p=None, pl='facebook', k='welcome', s='review'),
                           dict(p=7, pl='instagram', k='welcome', s='cancelled'),
                           dict(p=7, pl='instagram', k='welcome', s='review'),
                           dict(p=7, pl='facebook', k='roundup', s='review'),
                           dict(p=7, pl='facebook', k='welcome', s='review')])
            conn.execute(_MIGRATION.read_text())
            # The index lands on the shadow table, in this session's temp schema.
            made = conn.execute(
                """SELECT count(*) FROM pg_indexes
                    WHERE indexname = 'publishing_queue_one_live_welcome'
                      AND schemaname = (SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema())"""
            ).fetchone()[0]
        finally:
            _end(conn)
    assert made == 1
