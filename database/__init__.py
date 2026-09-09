from typing import Any, TypeVar
import os
import psycopg
import random
import threading
import time
import traceback

DB_HOST = os.environ['DUO_DB_HOST']
DB_PORT = os.environ['DUO_DB_PORT']
DB_USER = os.environ['DUO_DB_USER']
DB_PASS = os.environ['DUO_DB_PASS']

_valid_isolation_levels = [
    'SERIALIZABLE',
    'REPEATABLE READ',
    'READ COMMITTED',
]

_default_transaction_isolation = 'REPEATABLE READ'

_coninfo_args = dict(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASS,
    options=(
        f" -c default_transaction_isolation=" +
            _default_transaction_isolation.replace(' ', '\\ ') +
        f" -c idle_session_timeout=0"
        f" -c statement_timeout=5000"
    ),
)

_api_conninfo = psycopg.conninfo.make_conninfo(
    **(_coninfo_args | dict(dbname='duo_api'))
)

_api_conn  = None

_api_conn_lock  = threading.Lock()

class api_tx:
    def __init__(self, isolation_level=_default_transaction_isolation):
        normalized_isolation_level = isolation_level.upper()

        if normalized_isolation_level not in _valid_isolation_levels:
            raise ValueError(isolation_level)

        self.isolation_level = normalized_isolation_level

        self.cur = None

    def __enter__(self):
        global _api_conn
        _api_conn_lock.acquire()
        try:
            if not _api_conn or _api_conn.closed:
                _api_conn = psycopg.Connection.connect(
                    conninfo=_api_conninfo, row_factory=psycopg.rows.dict_row)
            if _api_conn.info.transaction_status in (
                psycopg.pq.TransactionStatus.INERROR,
                psycopg.pq.TransactionStatus.INTRANS,
            ):
                _api_conn.rollback()
            self.cur = _api_conn.cursor()
            if self.isolation_level != _default_transaction_isolation:
                self.cur.execute(f'SET TRANSACTION ISOLATION LEVEL {self.isolation_level}')
            return self.cur
        except BaseException:
            if _api_conn:
                try:
                    _api_conn.close()
                except BaseException:
                    pass
            _api_conn = None
            _api_conn_lock.release()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _api_conn
        # F03 (2026-09-07 review): a commit failure on a SUCCESSFUL body
        # must propagate. The old handler logged the body's exc-tuple
        # (None on success) and returned normally, so callers ran success
        # handling on data that never committed. Now the commit exception
        # is re-raised with its own traceback; a body exception still
        # takes precedence (we only capture the failure to re-raise when
        # the body itself succeeded); cursor close and lock release are
        # guaranteed in finally so the shared connection lock can never
        # leak on the error path.
        commit_error = None
        try:
            if exc_type is None:
                _api_conn.commit()
            else:
                _api_conn.rollback()
        except BaseException as e:
            print(traceback.format_exc())
            try:
                _api_conn.close()
            except BaseException:
                pass
            _api_conn = None
            if exc_type is None:
                commit_error = e
        finally:
            try:
                self.cur.close()
            except:
                print(traceback.format_exc())
            _api_conn_lock.release()

        if commit_error is not None:
            raise commit_error

RowT = TypeVar('RowT')

def fetchall_sets(tx: psycopg.Cursor[RowT]) -> list[RowT]:
    result: list[RowT] = []
    while True:
        result.extend(tx.fetchall())
        nextset = tx.nextset()
        if nextset is None:
            break
    return result

def _check_api_connection_forever():
    while True:
        try:
            with api_tx() as tx:
                tx.execute('SELECT 1')
        except:
            print(traceback.format_exc())
        time.sleep(random.randint(30, 90))

threading.Thread(target=_check_api_connection_forever,  daemon=True).start()
