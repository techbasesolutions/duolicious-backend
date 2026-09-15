"""F07/F08: the durable email outbox.

Every campaign and transactional email is now a row before it is a send.
The rules these tests pin down:

  * enqueue is the idempotency point (one row per campaign/campaign_id/person),
    not the SMTP call, so a retried caller cannot double send;
  * two drains racing never reserve the same row (FOR UPDATE SKIP LOCKED);
  * an SMTP failure retries on a backoff and lands in 'failed', never silently
    disappears;
  * a reservation whose process dies becomes 'acceptance_unknown' rather than
    being re-sent blind (visible uncertainty, F08);
  * suppression, footer unsubscribe and the frequency cap are re-checked at
    SEND time, not only at enqueue time;
  * the post-send hook (E3's reinvite stamp) runs on acceptance only.

Fixtures are built before any transaction is opened, and no test nests
api_tx. SMTP is always a local stub: nothing here touches the network.
"""
from __future__ import annotations

from uuid import uuid4

import psycopg
import psycopg.rows

import database
from database import api_tx
from service.campaigns import outbox


def _addr(label: str) -> str:
    """A mailable, reserved-TLD address. Unique per call so a re-run against
    the same warm test database can never collide on person.email."""
    return f'{label}-{uuid4().hex[:8]}@ahavah-test.invalid'


class _Smtp:
    def __init__(self, fail_times: int = 0):
        self.sent: list[dict] = []
        self.fail_times = fail_times

    def send(self, **kw):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError('smtp down')
        self.sent.append(kw)
        return 'mid-' + str(len(self.sent))


def _enqueue(pid, email, campaign='e1', cid='run-1', exempt=False, post_send=None,
             unsub_scope='notifications'):
    with api_tx() as tx:
        return outbox.enqueue(tx, campaign=campaign, campaign_id=cid, person_id=pid, email=email,
                              subject='s', html='<p>h</p>', from_addr='hello@ahavah.app',
                              unsub_scope=unsub_scope, exempt=exempt, post_send=post_send)


def _row(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT * FROM email_outbox WHERE person_id = %(p)s ORDER BY id DESC LIMIT 1",
                          dict(p=pid)).fetchone()


def _only(*person_ids):
    """Park every other due row so a drain in this test can only pick up the
    rows this test enqueued. The suite shares one database and one outbox
    table, so without this a drain would also pick up whatever an earlier
    test left queued and the per-test counts would be meaningless."""
    with api_tx() as tx:
        tx.execute(
            """UPDATE email_outbox SET next_attempt_at = NOW() + interval '1 hour'
                WHERE state = 'queued' AND NOT (person_id = ANY(%(ids)s))""",
            dict(ids=list(person_ids)))


def test_enqueue_is_the_idempotency_point(make_person):
    email = _addr('once')
    p = make_person(name='Once', email=email)
    assert _enqueue(p['id'], email) is not None
    assert _enqueue(p['id'], email) is None
    _only(p['id'])
    smtp = _Smtp()
    outbox.drain(api_tx, smtp)
    outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert len(smtp.sent) == 1 and r['state'] == 'accepted' and r['provider_message_id'] == 'mid-1'
    assert smtp.sent[0]['to_addr'] == email
    assert smtp.sent[0]['body'] == '<p>h</p>' and smtp.sent[0]['subject'] == 's'


def test_concurrent_drains_never_double_send(make_person):
    e1, e2 = _addr('c1'), _addr('c2')
    p1 = make_person(name='C1', email=e1)
    p2 = make_person(name='C2', email=e2)
    _enqueue(p1['id'], e1, cid='conc')
    _enqueue(p2['id'], e2, cid='conc')
    a = psycopg.connect(database._api_conninfo, row_factory=psycopg.rows.dict_row)
    b = psycopg.connect(database._api_conninfo, row_factory=psycopg.rows.dict_row)
    try:
        first = outbox.reserve(a, limit=1)          # holds its row lock until commit
        second = outbox.reserve(b, limit=5)
        assert len(first) == 1
        assert all(r['id'] != first[0]['id'] for r in second)   # SKIP LOCKED: never the same row twice
    finally:
        a.rollback(); a.close(); b.rollback(); b.close()


def test_smtp_failure_retries_then_fails(make_person):
    email = _addr('retry')
    p = make_person(name='Retry', email=email)
    _enqueue(p['id'], email, cid='retry-1')
    _only(p['id'])
    smtp = _Smtp(fail_times=3)
    for _ in range(3):
        with api_tx() as tx:
            tx.execute("UPDATE email_outbox SET next_attempt_at = NOW() WHERE person_id = %(p)s",
                       dict(p=p['id']))
        outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert r['state'] == 'failed' and r['attempts'] == 3 and 'smtp down' in r['last_error']
    assert smtp.sent == []


def test_smtp_failure_backs_off_before_the_next_attempt(make_person):
    """The retry is not immediate: a failed attempt goes back to 'queued'
    with next_attempt_at pushed out, so the very next drain does not hammer
    a mail server that just refused."""
    email = _addr('backoff')
    p = make_person(name='Backoff', email=email)
    _enqueue(p['id'], email, cid='backoff-1')
    _only(p['id'])
    smtp = _Smtp(fail_times=1)
    out = outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert out['failed'] == 1 and r['state'] == 'queued' and r['attempts'] == 1
    assert outbox.drain(api_tx, smtp)['reserved'] == 0     # still backing off
    with api_tx('read committed') as tx:
        due = tx.execute("SELECT next_attempt_at > NOW() AS later FROM email_outbox WHERE id = %(i)s",
                         dict(i=r['id'])).fetchone()
    assert due['later'] is True


def test_reservation_that_never_completes_becomes_acceptance_unknown(make_person):
    email = _addr('lost')
    p = make_person(name='Lost', email=email)
    _enqueue(p['id'], email, cid='lost-1')
    _only(p['id'])
    with api_tx() as tx:
        outbox.reserve(tx)                      # reserved, process "dies" here
    with api_tx() as tx:
        tx.execute("UPDATE email_outbox SET reserved_at = NOW() - interval '11 minutes' WHERE person_id = %(p)s",
                   dict(p=p['id']))
    smtp = _Smtp()
    out = outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert out['unknown_reaped'] == 1 and r['state'] == 'acceptance_unknown' and smtp.sent == []
    assert r['last_error'] == 'reservation expired'


def test_suppression_and_unsubscribe_checked_at_send_time(make_person):
    email = _addr('unsub')
    p = make_person(name='Unsub', email=email)
    with api_tx() as tx:
        outbox.enqueue(tx, campaign='e2', campaign_id='w1', person_id=p['id'],
                       email=email, subject='s', html='<p>h</p>',
                       from_addr='hello@ahavah.app', unsub_scope='community')
        tx.execute("UPDATE person SET community_unsubscribed_at = NOW() WHERE id = %(p)s", dict(p=p['id']))
    _only(p['id'])
    smtp = _Smtp()
    out = outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert smtp.sent == [] and r['state'] == 'skipped' and r['last_error'] == 'unsubscribed'
    assert out['skipped'] == 1


def test_an_address_suppressed_after_enqueue_is_never_sent(make_person):
    """The suppression list is re-read at drain time: a row whose stored
    address is on the no-send list by the time the drain reaches it is
    skipped, not sent."""
    p = make_person(name='Supp', email=_addr('supp'))
    with api_tx() as tx:
        outbox.enqueue(tx, campaign='e1', campaign_id='supp-1', person_id=p['id'],
                       email=f"supp-{p['id']}@techbaseltd.com", subject='s', html='<p>h</p>',
                       from_addr='hello@ahavah.app', unsub_scope='notifications')
    _only(p['id'])
    smtp = _Smtp()
    out = outbox.drain(api_tx, smtp)
    r = _row(p['id'])
    assert smtp.sent == [] and r['state'] == 'skipped' and r['last_error'] == 'suppressed'
    assert out['skipped'] == 1


def test_the_frequency_cap_is_rechecked_at_send_time(make_person):
    """A member mailed by another campaign between enqueue and drain is
    capped at send time rather than mailed twice in the window. An exempt
    row (E4/E5, member-triggered) is not."""
    from service.campaigns import log_send

    capped_email, free_email = _addr('capped'), _addr('exempt')
    capped = make_person(name='Capped', email=capped_email)
    free = make_person(name='Exempt', email=free_email)
    _enqueue(capped['id'], capped_email, cid='cap-1')
    _enqueue(free['id'], free_email, campaign='e4', cid='cap-e4', exempt=True)
    with api_tx() as tx:
        log_send(tx, capped['id'], 'e2', 'other-run', 'mid')
        log_send(tx, free['id'], 'e2', 'other-run', 'mid')
    _only(capped['id'], free['id'])
    smtp = _Smtp()
    out = outbox.drain(api_tx, smtp)
    assert _row(capped['id'])['state'] == 'skipped' and _row(capped['id'])['last_error'] == 'capped'
    assert _row(free['id'])['state'] == 'accepted'
    assert len(smtp.sent) == 1 and out['skipped'] == 1 and out['accepted'] == 1


def test_acceptance_writes_the_send_log(make_person):
    email = _addr('logged')
    p = make_person(name='Logged', email=email)
    _enqueue(p['id'], email, cid='logged-1')
    _only(p['id'])
    outbox.drain(api_tx, _Smtp())
    with api_tx('read committed') as tx:
        row = tx.execute(
            """SELECT message_id FROM email_send_log
                WHERE person_id = %(p)s AND campaign = 'e1' AND campaign_id = 'logged-1'""",
            dict(p=p['id'])).fetchone()
    assert row is not None and row['message_id'] == 'mid-1'


def test_post_send_hook_runs_on_acceptance_only(make_person):
    email = _addr('hook')
    p = make_person(name='Hook', email=email)
    with api_tx() as tx:
        outbox.enqueue(tx, campaign='e3', campaign_id='r1', person_id=p['id'],
                       email=email, subject='s', html='<p>h</p>',
                       from_addr='hello@ahavah.app', unsub_scope='notifications',
                       post_send='reinvite_sent_at')
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT reinvite_sent_at FROM person WHERE id = %(p)s",
                          dict(p=p['id'])).fetchone()['reinvite_sent_at'] is None
    _only(p['id'])
    outbox.drain(api_tx, _Smtp())
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT reinvite_sent_at FROM person WHERE id = %(p)s",
                          dict(p=p['id'])).fetchone()['reinvite_sent_at'] is not None


def test_post_send_hook_does_not_run_for_a_skipped_row(make_person):
    email = _addr('hookskip')
    p = make_person(name='HookSkip', email=email)
    with api_tx() as tx:
        outbox.enqueue(tx, campaign='e3', campaign_id='r2', person_id=p['id'],
                       email=email, subject='s', html='<p>h</p>',
                       from_addr='hello@ahavah.app', unsub_scope='community',
                       post_send='reinvite_sent_at')
        tx.execute("UPDATE person SET community_unsubscribed_at = NOW() WHERE id = %(p)s", dict(p=p['id']))
    _only(p['id'])
    outbox.drain(api_tx, _Smtp())
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT reinvite_sent_at FROM person WHERE id = %(p)s",
                          dict(p=p['id'])).fetchone()['reinvite_sent_at'] is None


def test_status_counts(make_person):
    ea, eb = _addr('stata'), _addr('statb')
    a = make_person(name='StatA', email=ea)
    b = make_person(name='StatB', email=eb)
    _enqueue(a['id'], ea, cid='stat-1')
    _enqueue(b['id'], eb, cid='stat-1')
    _only(a['id'], b['id'])
    with api_tx('read committed') as tx:
        assert outbox.status(tx, 'e1', 'stat-1') == dict(
            queued=2, reserved=0, accepted=0, acceptance_unknown=0, failed=0, skipped=0)
    outbox.drain(api_tx, _Smtp(), limit=1)
    with api_tx('read committed') as tx:
        assert outbox.status(tx, 'e1', 'stat-1') == dict(
            queued=1, reserved=0, accepted=1, acceptance_unknown=0, failed=0, skipped=0)


def test_list_unsubscribe_and_from_addr_ride_the_payload(make_person):
    email = _addr('hdr')
    p = make_person(name='Hdr', email=email)
    with api_tx() as tx:
        outbox.enqueue(tx, campaign='e1', campaign_id='hdr-1', person_id=p['id'],
                       email=email, subject='s', html='<p>h</p>',
                       from_addr='support@ahavah.app', unsub_scope='notifications',
                       list_unsubscribe='<https://ahavah.app/u/notifications.abc>')
    _only(p['id'])
    smtp = _Smtp()
    outbox.drain(api_tx, smtp)
    assert smtp.sent[0]['from_addr'] == 'support@ahavah.app'
    assert smtp.sent[0]['list_unsubscribe'] == '<https://ahavah.app/u/notifications.abc>'


# ---------------------------------------------------------------------------
# Fix round 1, ruling 1: a drain reserves ONE row at a time. A batch
# reservation meant a process that died mid-batch stranded every row it had
# claimed; reserving per iteration strands at most the one actually in flight.
# ---------------------------------------------------------------------------

class _Abort(BaseException):
    """Not an Exception: `drain`'s own `except Exception` (the retryable-SMTP
    path) must not catch this, so it stands in for the process genuinely
    going away mid-send."""


class _AbortingSmtp:
    def __init__(self, abort_on: int):
        self.sent: list[dict] = []
        self.abort_on = abort_on

    def send(self, **kw):
        self.sent.append(kw)
        if len(self.sent) == self.abort_on:
            raise _Abort('process died mid-send')
        return 'mid-' + str(len(self.sent))


def _states(person_ids):
    with api_tx('read committed') as tx:
        return {r['person_id']: r['state'] for r in tx.execute(
            """SELECT person_id, state FROM email_outbox
                WHERE person_id = ANY(%(ids)s) ORDER BY id""",
            dict(ids=list(person_ids))).fetchall()}


def test_a_drain_that_dies_mid_send_strands_exactly_one_row(make_person):
    addrs = [_addr('strand1'), _addr('strand2'), _addr('strand3')]
    people = [make_person(name=f'Strand{i}', email=a) for i, a in enumerate(addrs)]
    for person, addr in zip(people, addrs):
        _enqueue(person['id'], addr, cid='strand')
    ids = [p['id'] for p in people]
    _only(*ids)
    smtp = _AbortingSmtp(abort_on=2)
    try:
        outbox.drain(api_tx, smtp)
    except _Abort:
        pass
    else:
        raise AssertionError('the stub should have aborted the drain')
    states = _states(ids)
    assert states[ids[0]] == 'accepted'      # already finished, unaffected
    assert states[ids[1]] == 'reserved'      # the one genuinely in flight
    assert states[ids[2]] == 'queued'        # never claimed, free for the next drain
    assert len(smtp.sent) == 2


def test_the_next_drain_picks_up_where_a_dead_one_stopped(make_person):
    """The rows a dead drain never claimed are still 'queued', so the very
    next drain sends them without waiting out the reservation timeout."""
    addrs = [_addr('resume1'), _addr('resume2')]
    people = [make_person(name=f'Resume{i}', email=a) for i, a in enumerate(addrs)]
    for person, addr in zip(people, addrs):
        _enqueue(person['id'], addr, cid='resume')
    ids = [p['id'] for p in people]
    _only(*ids)
    try:
        outbox.drain(api_tx, _AbortingSmtp(abort_on=1))
    except _Abort:
        pass
    assert _states(ids)[ids[1]] == 'queued'
    smtp = _Smtp()
    out = outbox.drain(api_tx, smtp)
    assert out['reserved'] == 1 and len(smtp.sent) == 1
    assert _states(ids)[ids[1]] == 'accepted'


def test_a_send_that_was_never_recorded_is_never_sent_again(make_person):
    """Crash window (b): SMTP accepted the message but the process died
    before `mark_accepted` committed. Nobody knows whether it arrived, so the
    reaper marks it `acceptance_unknown` and NO second send is attempted."""
    email = _addr('unrecorded')
    p = make_person(name='Unrecorded', email=email)
    _enqueue(p['id'], email, cid='unrecorded-1')
    _only(p['id'])

    first = _Smtp()
    with api_tx() as tx:
        rows = outbox.reserve(tx, limit=1)
    assert len(rows) == 1
    payload = rows[0]['payload']
    first.send(subject=payload['subject'], body=payload['html'], to_addr=rows[0]['email'],
               from_addr=payload['from_addr'], list_unsubscribe=payload['list_unsubscribe'])
    # mark_accepted deliberately NOT called: this is the crash.
    with api_tx() as tx:
        tx.execute("UPDATE email_outbox SET reserved_at = NOW() - interval '11 minutes' WHERE id = %(i)s",
                   dict(i=rows[0]['id']))

    second = _Smtp()
    out = outbox.drain(api_tx, second)
    assert out['unknown_reaped'] == 1 and out['reserved'] == 0
    assert second.sent == []
    assert len(first.sent) == 1
    assert _row(p['id'])['state'] == 'acceptance_unknown'


def test_status_buckets_a_state_it_does_not_know(make_person):
    """Forward compatibility: a row written by a newer deploy, in a state
    this one has never heard of, must not blow up the admin dashboard."""
    email = _addr('unknownstate')
    p = make_person(name='UnknownState', email=email)
    row_id = _enqueue(p['id'], email, cid='unknown-state-1')
    with api_tx() as tx:
        tx.execute("ALTER TABLE email_outbox DROP CONSTRAINT email_outbox_state_check")
        try:
            tx.execute("UPDATE email_outbox SET state = 'quarantined' WHERE id = %(i)s", dict(i=row_id))
            counts = outbox.status(tx, 'e1', 'unknown-state-1')
        finally:
            tx.execute("UPDATE email_outbox SET state = 'queued' WHERE id = %(i)s", dict(i=row_id))
            tx.execute("""ALTER TABLE email_outbox ADD CONSTRAINT email_outbox_state_check
                          CHECK (state IN ('queued','reserved','accepted','acceptance_unknown','failed','skipped'))""")
    assert counts['other'] == 1
    assert counts['queued'] == 0 and counts['accepted'] == 0
