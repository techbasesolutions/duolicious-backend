"""Migration 0043: the campaign_link kind index (I7) and the widened claim
selection that retries a transient failure (I8, spec 5).

Rows are given a scheduled_for far in the past so they sort ahead of anything
an earlier test left due, and every assertion filters on this test's own
request_key: the test database persists between runs.
"""
import uuid

from database import api_tx


def test_0043_campaign_link_kind_index_exists():
    with api_tx('read committed') as tx:
        assert tx.execute(
            """SELECT 1 FROM pg_indexes
                WHERE tablename = 'campaign_link' AND indexname = 'campaign_link_kind_idx'"""
        ).fetchone()


def test_0043_claim_still_takes_a_due_scheduled_row():
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO publishing_queue (request_key, kind, platform, status, scheduled_for)
               VALUES (%(rk)s, 'roundup', 'facebook', 'scheduled', NOW() - interval '10 years'),
                      (%(rk)s, 'roundup', 'instagram', 'scheduled', NOW() + interval '1 hour')""",
            dict(rk=rk))
        mine = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(200)").fetchall()
                if r['request_key'] == rk]
    assert [r['platform'] for r in mine] == ['facebook']
    assert mine[0]['status'] == 'processing' and mine[0]['attempts'] == 1


def test_0043_failed_row_retries_until_its_attempts_are_spent():
    """A `failed` row is one nothing was sent for (an attempted call whose
    response was lost becomes `review`, never `failed`), so re-claiming it
    cannot double post. At three attempts it stays failed for a human."""
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO publishing_queue (request_key, kind, platform, status, attempts, scheduled_for)
               VALUES (%(rk)s, 'roundup', 'facebook', 'failed', 1, NOW() - interval '10 years'),
                      (%(rk)s, 'roundup', 'instagram', 'failed', 3, NOW() - interval '10 years')""",
            dict(rk=rk))
        mine = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(200)").fetchall()
                if r['request_key'] == rk]
        left = {r['platform']: r for r in tx.execute(
            "SELECT platform, status FROM publishing_queue WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchall()}
    assert [r['platform'] for r in mine] == ['facebook']
    assert mine[0]['status'] == 'processing' and mine[0]['attempts'] == 2
    assert left['instagram']['status'] == 'failed'


def test_0043_failed_row_is_not_claimed_before_its_slot():
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO publishing_queue (request_key, kind, platform, status, attempts, scheduled_for)
               VALUES (%(rk)s, 'roundup', 'facebook', 'failed', 0, NOW() + interval '1 hour')""",
            dict(rk=rk))
        mine = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(200)").fetchall()
                if r['request_key'] == rk]
    assert mine == []
