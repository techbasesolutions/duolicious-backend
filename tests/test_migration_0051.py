"""Migration 0051: render backoff columns on publishing_queue.

Wave 3d Task 4 (acceptance 8c): a card whose render keeps failing is backed
off (`render_attempts`, `render_next_attempt_at`) so it stops hiding older
work from the render tick, and the last sanitised failure reason is kept on
the row (`render_error`) apart from `error`, which publishing owns.

The re-apply check runs the file against a temporary table named
`publishing_queue` that already holds a production-shaped row: a temp table
shadows the real one for its own session, so the migration's SQL runs
unmodified without touching the shared table.
"""
from pathlib import Path

import psycopg

import database
from database import api_tx

_MIGRATION = Path(__file__).resolve().parent.parent / 'migrations' / '0051_spotlight_render_backoff.sql'


def test_0051_columns_exist_with_the_declared_shape():
    with api_tx('read committed') as tx:
        cols = {r['column_name']: r for r in tx.execute(
            """SELECT column_name, data_type, is_nullable, column_default
                 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'publishing_queue'
                  AND column_name IN ('render_attempts', 'render_next_attempt_at', 'render_error')"""
        ).fetchall()}
    assert set(cols) == {'render_attempts', 'render_next_attempt_at', 'render_error'}, \
        'migration 0051 has not been applied'
    assert cols['render_attempts']['data_type'] == 'integer'
    assert cols['render_attempts']['is_nullable'] == 'NO'
    assert cols['render_attempts']['column_default'] == '0'
    assert cols['render_next_attempt_at']['data_type'] == 'timestamp with time zone'
    assert cols['render_next_attempt_at']['is_nullable'] == 'YES'
    assert cols['render_error']['data_type'] == 'text'
    assert cols['render_error']['is_nullable'] == 'YES'


def test_0051_applies_twice_over_existing_rows():
    with psycopg.connect(database._api_conninfo, autocommit=True) as conn:
        try:
            conn.execute("""CREATE TEMP TABLE publishing_queue (
                                request_key text NOT NULL, platform text NOT NULL,
                                status text NOT NULL, error text)""")
            conn.execute("""INSERT INTO publishing_queue VALUES
                                ('rk-existing', 'facebook', 'awaiting_render', 'lease_expired')""")
            conn.execute(_MIGRATION.read_text())
            conn.execute(_MIGRATION.read_text())
            row = conn.execute(
                """SELECT render_attempts, render_next_attempt_at, render_error, error
                     FROM pg_temp.publishing_queue""").fetchone()
        finally:
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.execute('ROLLBACK')
            conn.execute('DROP TABLE IF EXISTS pg_temp.publishing_queue')
    assert row == (0, None, None, 'lease_expired')
