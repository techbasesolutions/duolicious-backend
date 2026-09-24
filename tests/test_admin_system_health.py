"""The outbox half of /admin/system/health.

Campaign email delivery is the one delivery signal this system actually
has, and until now nothing surfaced it. These tests run the real query
against the real table rather than asserting on its text, because the
whole point of the panel it feeds is that its numbers are measured.
"""
import hashlib
import json
import secrets
from datetime import datetime

import pytest

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


# ---------------------------------------------------------------------------
# Through the real Flask client, not by reading the route's source. An
# earlier version of this file asserted that the string "'outbox':
# dict(outbox)" appeared in the module, which verified no JSON shape at all
# and is why a wrong wire format for the timestamp went unnoticed: Flask's
# default provider serialises a bare datetime as an RFC 2822 HTTP-date, and
# the TypeScript reading it is typed and tested against ISO 8601.
#
# Admin fixtures borrowed from tests/test_growth_routes.py, which explains
# how a session bearer is minted.
# ---------------------------------------------------------------------------

def _bearer(person_id: int, email: str) -> dict:
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=person_id))
    return {'Authorization': f'Bearer {tok}'}


@pytest.fixture
def health_admin(make_person):
    p = make_person(name='HealthAdmin')
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s",
                   dict(i=p['id']))
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s",
                           dict(i=p['id'])).fetchone()['email']
    return dict(id=p['id'], headers=_bearer(p['id'], email))


def test_the_route_answers_with_every_outbox_state(client, health_admin):
    r = client.get('/admin/system/health', headers=health_admin['headers'])
    assert r.status_code == 200
    body = r.get_json()
    assert set(body['outbox']) == {
        'queued', 'reserved', 'accepted_24h', 'failed', 'skipped',
        'acceptance_unknown', 'oldest_queued_at'}
    for key, value in body['outbox'].items():
        if key != 'oldest_queued_at':
            assert isinstance(value, int), f'{key} is {value!r}'


def test_every_outbox_state_in_the_table_has_a_column(client, health_admin):
    """A message that exists in the table and appears on no line is worse
    than no panel: an operator reconciling 26 recipients against 20 accepted
    needs somewhere for the other six to be. `skipped` was missing at first
    review, which is exactly where the frequency cap puts them."""
    from service.campaigns import outbox as ob
    r = client.get('/admin/system/health', headers=health_admin['headers'])
    reported = set(r.get_json()['outbox'])
    for state in ob.STATES:
        key = 'accepted_24h' if state == 'accepted' else state
        assert key in reported, f"the outbox state {state!r} is on no line of the panel"


def test_the_queued_timestamp_is_iso_8601_on_the_wire(client, health_admin, make_person):
    """Flask's default provider would send an RFC 2822 HTTP-date here. Every
    other timestamp on the admin surface is ISO, and relativeAge in the admin
    repo is tested against ISO."""
    p = make_person(name='HealthQueued')
    with api_tx() as tx:
        _insert(tx, p['id'], 'queued', created_ago=2)
    r = client.get('/admin/system/health', headers=health_admin['headers'])
    stamp = r.get_json()['outbox']['oldest_queued_at']
    assert stamp is not None
    # Raises on an HTTP-date, which is the whole point.
    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_a_member_cannot_read_system_health(client, make_person):
    p = make_person(name='HealthMember')
    with api_tx('read committed') as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s",
                           dict(i=p['id'])).fetchone()['email']
    r = client.get('/admin/system/health', headers=_bearer(p['id'], email))
    assert r.status_code == 403


def test_otp_reports_codes_issued_and_claims_no_delivery():
    """Q_OTP_24H counts duo_session rows. Nothing in the system knows
    whether an OTP arrived, so this query must never grow a success or
    failure column to feed a rate onto the dashboard."""
    assert 'sent_24h' in Q_OTP_24H
    lowered = Q_OTP_24H.lower()
    assert 'fail' not in lowered and 'success' not in lowered
