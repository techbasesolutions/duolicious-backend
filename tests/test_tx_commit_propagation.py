"""F03 (2026-09-07 system review): a commit failure on a SUCCESSFUL body
must propagate, not be swallowed.

Before the fix, api_tx.__exit__ (and asyncdatabase.api_tx.__aexit__)
caught the commit exception, logged the body's exc-tuple (which is
(None, None, None) on a successful body, so it logged nothing useful),
and returned normally. Callers therefore ran success handling on data
the database never committed. This underpins matching, profile writes,
moderation, and payments.
"""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import psycopg
import pytest

import database
import database.asyncdatabase as asyncdatabase


def _fake_sync_conn(*, commit_error=None, rollback_error=None):
    """A stand-in for the shared sync connection that passes __enter__
    cleanly (not closed, idle tx status so the pre-rollback branch is
    skipped) and fails commit/rollback as configured."""
    fake = MagicMock()
    fake.closed = False
    fake.info.transaction_status = psycopg.pq.TransactionStatus.IDLE
    if commit_error is not None:
        fake.commit.side_effect = commit_error
    if rollback_error is not None:
        fake.rollback.side_effect = rollback_error
    return fake


def test_sync_commit_failure_propagates_and_conn_recovers():
    fake = _fake_sync_conn(commit_error=psycopg.OperationalError("commit boom"))
    with patch.object(database, "_api_conn", fake):
        with pytest.raises(psycopg.OperationalError):
            with database.api_tx():
                pass  # body succeeds; exc_type is None at __exit__

    # The process-wide lock must have been released even on the error
    # path, or this real transaction would deadlock. It also proves the
    # connection recovers for the next caller.
    assert not database._api_conn_lock.locked()
    with database.api_tx() as tx:
        assert tx.execute("SELECT 1 AS ok").fetchone()["ok"] == 1


def test_sync_body_exception_takes_precedence_over_rollback_failure():
    # Body raises; rollback ALSO fails. The original body exception must
    # win, not be replaced by the rollback error.
    fake = _fake_sync_conn(rollback_error=psycopg.OperationalError("rollback boom"))
    with patch.object(database, "_api_conn", fake):
        with pytest.raises(ValueError, match="body failed"):
            with database.api_tx():
                raise ValueError("body failed")
    assert not database._api_conn_lock.locked()


def test_async_commit_failure_propagates():
    async def _run():
        fake = MagicMock()
        fake.closed = False
        fake.commit = AsyncMock(side_effect=psycopg.OperationalError("async commit boom"))
        fake.cursor.return_value.close = AsyncMock()
        with patch.object(asyncdatabase, "_api_conn", fake):
            with pytest.raises(psycopg.OperationalError):
                async with asyncdatabase.api_tx():
                    pass
            # Lock released on the error path so the next async tx is not
            # blocked forever.
            assert not asyncdatabase._api_conn_lock.locked()

    asyncio.run(_run())
