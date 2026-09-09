from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock, patch

import psycopg
import pytest
import database
from database import api_tx
from service.decisions import Q_RECORD_LIKE, _check_like_quota
from service.decisions.locking import lock_like_members
from service.entitlements import has_entitlement, list_entitlements


def test_expiry_is_enforced_without_running_reconciliation(make_person):
    person = make_person()
    with api_tx() as tx:
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NOW() - interval '1 second' WHERE id = %(id)s", person)
    assert not has_entitlement(person['id'], 'premium')
    assert list_entitlements(person['id']) == []
    with api_tx() as tx:
        tx.execute("UPDATE person SET subscription_expires_at = NULL WHERE id = %(id)s", person)
    assert has_entitlement(person['id'], 'premium')


def _concurrent_likes(pairs, quota=False):
    barrier = Barrier(2)
    def worker(pair):
        me, peer = pair
        with psycopg.connect(database._api_conninfo, row_factory=psycopg.rows.dict_row) as conn:
            with conn.cursor() as tx:
                tx.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
                barrier.wait(timeout=10)
                lock_like_members(tx, me['id'], peer['uuid'])
                if quota and _check_like_quota(tx, me['id'], me['uuid'], []):
                    return 'quota'
                return tx.execute(Q_RECORD_LIKE, dict(me_id=me['id'], prospect_uuid=peer['uuid'])).fetchone()
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(worker, pairs))


def test_simultaneous_first_likes_form_one_match(make_person):
    a, b = make_person(), make_person()
    results = _concurrent_likes([(a, b), (b, a)])
    assert sum(bool(r['was_new_match']) for r in results) == 1
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM ahavah_match WHERE user_a_id = LEAST(%(a)s,%(b)s) "
                          "AND user_b_id = GREATEST(%(a)s,%(b)s)", dict(a=a['id'], b=b['id'])).fetchone()['n'] == 1


def test_simultaneous_likes_cannot_exceed_daily_quota(make_person):
    a = make_person()
    peers = [make_person() for _ in range(11)]
    with api_tx() as tx:
        for peer in peers[:9]:
            tx.execute('INSERT INTO liked (liker_id, liked_id) VALUES (%(a)s,%(b)s)', dict(a=a['id'], b=peer['id']))
    results = _concurrent_likes([(a, peers[9]), (a, peers[10])], quota=True)
    assert results.count('quota') == 1
    with api_tx() as tx:
        assert tx.execute('SELECT count(*) AS n FROM liked WHERE liker_id = %(id)s', a).fetchone()['n'] == 10


def test_cursor_failure_discards_connection_and_releases_lock():
    conn = MagicMock()
    conn.closed = False
    conn.info.transaction_status = psycopg.pq.TransactionStatus.IDLE
    conn.cursor.side_effect = psycopg.OperationalError('cursor unavailable')
    with patch.object(database, '_api_conn', conn):
        with pytest.raises(psycopg.OperationalError):
            with api_tx():
                pass
        assert database._api_conn is None
        conn.close.assert_called_once()
        assert not database._api_conn_lock.locked()
