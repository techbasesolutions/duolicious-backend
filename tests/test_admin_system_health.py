"""The outbox half of /admin/system/health.

Campaign email delivery is the one delivery signal this system actually
has, and until now nothing surfaced it. These tests run the real query
against the real table rather than asserting on its text, because the
whole point of the panel it feeds is that its numbers are measured.
"""
import json

from database import api_tx
from service.admin.queries import Q_OUTBOX_HEALTH, Q_OTP_24H


def _insert(tx, person_id, state, *, sent_ago=None, created_ago=None):
    """One outbox row in a given state. `sent_at` is set only for accepted
    rows, which mirrors the drain: service/campaigns/outbox.py sets it on
    acceptance and nowhere else."""
    tx.execute(
        """INSERT INTO email_outbox
             (campaign, campaign_id, person_id, email, payload, unsub_scope,
              state, sent_at, created_at)
           VALUES (%(c)s, %(cid)s, %(pid)s, %(em)s, %(pl)s, 'community',
                   %(st)s,
                   CASE WHEN %(sent)s::int IS NULL THEN NULL
                        ELSE NOW() - make_interval(hours => %(sent)s::int) END,
                   NOW() - make_interval(hours => %(made)s::int))""",
        dict(c='e2', cid=f'health-{state}-{person_id}', pid=person_id,
             em=f'health-{person_id}@ahavah-test.invalid',
             pl=json.dumps(dict(subject='x', html='<p>x</p>')),
             st=state, sent=sent_ago, made=created_ago or 0))


def _health(tx):
    return dict(tx.execute(Q_OUTBOX_HEALTH).fetchone())


def test_the_query_counts_each_state_separately(make_person):
    people = [make_person(name=f'H{n}') for n in range(5)]
    with api_tx() as tx:
        before = _health(tx)
        _insert(tx, people[0]['id'], 'queued', created_ago=3)
        _insert(tx, people[1]['id'], 'reserved')
        _insert(tx, people[2]['id'], 'accepted', sent_ago=2)
        _insert(tx, people[3]['id'], 'failed')
        _insert(tx, people[4]['id'], 'acceptance_unknown')
        after = _health(tx)

    def delta(key):
        return int(after[key]) - int(before[key])

    assert delta('queued') == 1
    assert delta('reserved') == 1
    assert delta('accepted_24h') == 1
    assert delta('failed') == 1
    assert delta('acceptance_unknown') == 1


def test_unknown_is_never_folded_into_failed(make_person):
    """They mean opposite things to an operator: failed is safe to retry,
    unknown may already have reached the member. A panel that showed one
    number for both would make a double send look like a safe retry."""
    p = make_person(name='Unknown')
    with api_tx() as tx:
        before = _health(tx)
        _insert(tx, p['id'], 'acceptance_unknown')
        after = _health(tx)
    assert int(after['acceptance_unknown']) == int(before['acceptance_unknown']) + 1
    assert int(after['failed']) == int(before['failed'])


def test_an_acceptance_older_than_a_day_is_outside_the_window(make_person):
    p = make_person(name='Old')
    with api_tx() as tx:
        before = _health(tx)
        _insert(tx, p['id'], 'accepted', sent_ago=25)
        after = _health(tx)
    assert int(after['accepted_24h']) == int(before['accepted_24h'])


def test_oldest_queued_at_is_the_oldest_waiting_row(make_person):
    a, b = make_person(name='QA'), make_person(name='QB')
    with api_tx() as tx:
        _insert(tx, a['id'], 'queued', created_ago=5)
        _insert(tx, b['id'], 'queued', created_ago=1)
        row = _health(tx)
        newest = tx.execute(
            "SELECT NOW() - make_interval(hours => 4) AS t").fetchone()['t']
    assert row['oldest_queued_at'] is not None
    # The 5-hour-old row wins over the 1-hour-old one.
    assert row['oldest_queued_at'] < newest


def test_the_route_returns_the_outbox_block():
    import service.api.admin.system_routes as sr
    assert hasattr(sr, 'get_system_health')
    # The route must read the outbox inside the SAME transaction as the
    # rest: the api connection lock is not reentrant.
    src = open(sr.__file__, encoding='utf-8').read()
    assert "'outbox': dict(outbox)" in src
    assert src.count('api_tx(') == 1


def test_otp_reports_codes_issued_and_claims_no_delivery():
    """Q_OTP_24H counts duo_session rows. Nothing in the system knows
    whether an OTP arrived, so this query must never grow a success or
    failure column to feed a rate onto the dashboard."""
    assert 'sent_24h' in Q_OTP_24H
    lowered = Q_OTP_24H.lower()
    assert 'fail' not in lowered and 'success' not in lowered
