"""Migration 0049: `cleanup_job.updated_at timestamptz NOT NULL DEFAULT NOW()`.

Fix wave: 0048 got a migration test in Wave 3b and 0049 did not, although it
is the migration with the tighter constraint of the two (NOT NULL with a
default that backfills every existing row in the same statement, rather than
0048's plainly nullable columns). This mirrors 0048's tests: the column
exists with the shape the migration claims, the default actually applies on
an insert that names no value, and the NOT NULL is real rather than assumed.
"""
import uuid

import psycopg
import pytest

from database import api_tx


def test_updated_at_column_exists_with_the_declared_shape():
    with api_tx() as tx:
        row = tx.execute("""
            SELECT data_type, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_name = 'cleanup_job' AND column_name = 'updated_at'
        """).fetchone()
    assert row is not None, 'migration 0049 has not been applied'
    assert row['data_type'] == 'timestamp with time zone'
    assert row['is_nullable'] == 'NO'
    assert 'now()' in (row['column_default'] or '').lower()


def test_a_new_job_gets_updated_at_without_naming_it():
    # api_tx() commits for real against the shared dev database, so the target
    # is a fresh value per run rather than a literal that a previous run of
    # this same test already wrote.
    target = f'fix-wave-0049/{uuid.uuid4()}'
    with api_tx() as tx:
        tx.execute(
            "INSERT INTO cleanup_job (kind, target) VALUES ('asset_delete', %(t)s)",
            dict(t=target))
    with api_tx() as tx:
        row = tx.execute("SELECT updated_at FROM cleanup_job WHERE target = %(t)s",
                         dict(t=target)).fetchone()
    assert row['updated_at'] is not None


def test_updated_at_cannot_be_set_null():
    """The NOT NULL is load-bearing, not decorative: `abandoned_job_rows`
    reports `updated_at` as "when did this job last do anything", and a null
    there would read as an answer rather than as a missing one."""
    target = f'fix-wave-0049-null/{uuid.uuid4()}'
    with pytest.raises(psycopg.errors.NotNullViolation):
        with api_tx() as tx:
            tx.execute(
                "INSERT INTO cleanup_job (kind, target, updated_at) VALUES ('asset_delete', %(t)s, NULL)",
                dict(t=target))
