"""Migration 0047: cleanup_job uniqueness is partial over pending jobs.

0046 shipped an unconditional UNIQUE (kind, target), which makes the FIRST
job for a key the only one there will ever be. Spotlight object keys are
content-hashed, so the same key legitimately comes back (a re-upload of
identical bytes produces an identical key) and has to be retirable again.
"""
from database import api_tx


def _indexes(tx, table) -> dict:
    return {r['indexname']: r['indexdef'] for r in tx.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %(t)s", dict(t=table)).fetchall()}


def _constraints(tx, table) -> set:
    return {r['conname'] for r in tx.execute(
        """SELECT c.conname FROM pg_constraint c
             JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = %(t)s""", dict(t=table)).fetchall()}


def test_0047_replaces_the_unconditional_unique_with_a_pending_only_one():
    with api_tx('read committed') as tx:
        indexes = _indexes(tx, 'cleanup_job')
        constraints = _constraints(tx, 'cleanup_job')
    assert 'cleanup_job_kind_target_key' not in constraints
    assert 'cleanup_job_kind_target_key' not in indexes
    definition = indexes['cleanup_job_pending_target_uidx']
    assert 'UNIQUE' in definition
    assert 'kind' in definition and 'target' in definition
    assert "WHERE (state = 'pending'::text)" in definition


def test_0047_allows_a_second_job_once_the_first_is_not_pending():
    """The behaviour the index exists for, asserted directly against the
    table rather than through `enqueue_asset_delete`."""
    from uuid import uuid4
    import psycopg
    import pytest
    target = f'test/{uuid4().hex}/reusable.png'
    with api_tx() as tx:
        tx.execute("INSERT INTO cleanup_job (kind, target) VALUES ('asset_delete', %(t)s)", dict(t=target))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            tx.execute("INSERT INTO cleanup_job (kind, target) VALUES ('asset_delete', %(t)s)", dict(t=target))
    with api_tx() as tx:
        tx.execute("UPDATE cleanup_job SET state = 'done', done_at = NOW() WHERE target = %(t)s", dict(t=target))
        tx.execute("INSERT INTO cleanup_job (kind, target) VALUES ('asset_delete', %(t)s)", dict(t=target))
    with api_tx('read committed') as tx:
        states = [r['state'] for r in tx.execute(
            "SELECT state FROM cleanup_job WHERE target = %(t)s ORDER BY id", dict(t=target)).fetchall()]
    assert states == ['done', 'pending']
