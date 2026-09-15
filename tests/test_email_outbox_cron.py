"""The `emailoutbox` cron: the one process that speaks SMTP (F07).

Two things are worth pinning down here and nowhere else. First, the cheap
due-row probe (`_has_work`) has to be true for BOTH kinds of work -- a due
message and a stale reservation -- or a dead drain's rows would sit in
'reserved' forever on an otherwise quiet queue. Second, `_drain_once` must
not construct an SMTP client on an empty tick: the loop runs every 30 seconds
all day, and authenticating to the mail provider each time for nothing is a
real cost.

`smtp.make_aws_smtp` is monkeypatched in every test that could reach it, so
nothing here opens a network connection.
"""
from __future__ import annotations

from uuid import uuid4

import smtp as smtp_mod
from database import api_tx
from service.campaigns import outbox
from service.cron.emailoutbox import EMAIL_OUTBOX_POLL_SECONDS, _drain_once, _has_work


class _StubSmtp:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, **kw):
        self.sent.append(kw)
        return f'mid-{len(self.sent)}'


def _addr(label: str) -> str:
    return f'{label}-{uuid4().hex[:8]}@ahavah-test.invalid'


def _park_all_but(*person_ids: int) -> None:
    with api_tx() as tx:
        tx.execute(
            """UPDATE email_outbox SET next_attempt_at = NOW() + interval '1 hour'
                WHERE state = 'queued' AND NOT (person_id = ANY(%(ids)s))""",
            dict(ids=list(person_ids)))


def _quiesce() -> None:
    """Park every due row and settle every stale reservation, so `_has_work`
    is answering about THIS test's rows and not an earlier test's leftovers."""
    _park_all_but()
    with api_tx() as tx:
        outbox.reap_reserved(tx)


def _enqueue(person_id: int, email: str, cid: str) -> int:
    with api_tx() as tx:
        return outbox.enqueue(tx, campaign='e1', campaign_id=cid, person_id=person_id,
                              email=email, subject='s', html='<p>h</p>',
                              from_addr='hello@ahavah.app', unsub_scope='notifications')


def test_poll_interval_is_configurable_and_defaults_to_thirty_seconds():
    assert EMAIL_OUTBOX_POLL_SECONDS == 30


def test_has_work_is_false_on_a_quiet_queue():
    _quiesce()
    assert _has_work() is False


def test_has_work_sees_a_due_message(make_person):
    email = _addr('cron-due')
    p = make_person(name='CronDue', email=email)
    _enqueue(p['id'], email, 'cron-due-1')
    _park_all_but(p['id'])
    assert _has_work() is True


def test_has_work_ignores_a_message_that_is_still_backing_off(make_person):
    email = _addr('cron-later')
    p = make_person(name='CronLater', email=email)
    row_id = _enqueue(p['id'], email, 'cron-later-1')
    _quiesce()
    with api_tx() as tx:
        tx.execute("UPDATE email_outbox SET next_attempt_at = NOW() + interval '10 minutes' WHERE id = %(i)s",
                   dict(i=row_id))
    assert _has_work() is False


def test_has_work_sees_a_stale_reservation_even_with_nothing_queued(make_person):
    """The reaper is work too: without this the `acceptance_unknown` sweep
    would never run on a queue that has gone quiet, which is exactly when a
    dead drain's rows are sitting there."""
    email = _addr('cron-stale')
    p = make_person(name='CronStale', email=email)
    row_id = _enqueue(p['id'], email, 'cron-stale-1')
    _quiesce()
    with api_tx() as tx:
        tx.execute(
            """UPDATE email_outbox SET state = 'reserved', reserved_at = NOW() - interval '11 minutes'
                WHERE id = %(i)s""", dict(i=row_id))
    assert _has_work() is True
    _drain_once()
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT state FROM email_outbox WHERE id = %(i)s",
                          dict(i=row_id)).fetchone()['state'] == 'acceptance_unknown'


def test_drain_once_builds_no_smtp_client_when_there_is_nothing_to_do(monkeypatch):
    _quiesce()
    built = []

    def _boom():
        built.append(1)
        raise AssertionError('an empty tick must not construct an SMTP client')

    monkeypatch.setattr(smtp_mod, 'make_aws_smtp', _boom)
    _drain_once()
    assert built == []


def test_drain_once_sends_the_due_message(make_person, monkeypatch):
    email = _addr('cron-send')
    p = make_person(name='CronSend', email=email)
    _enqueue(p['id'], email, 'cron-send-1')
    _park_all_but(p['id'])
    stub = _StubSmtp()
    monkeypatch.setattr(smtp_mod, 'make_aws_smtp', lambda: stub)
    _drain_once()
    assert len(stub.sent) == 1 and stub.sent[0]['to_addr'] == email
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT state FROM email_outbox WHERE person_id = %(p)s",
                          dict(p=p['id'])).fetchone()['state'] == 'accepted'
