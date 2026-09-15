"""The campaign runner no longer sends: it QUEUES (F07).

`run_campaign(send=True)` writes one `email_outbox` row per recipient and
returns; the outbox drain does the SMTP. So the counter these tests read
changed shape:

  * `queued` -- rows this run actually added. A second run with the same
    campaign_id adds none, which is the idempotency guarantee that used to
    live in the SMTP loop.
  * `built`  -- messages successfully built. A DRY RUN builds everything and
    queues nothing, so `built` is the only number a dry run can report, and
    it carries exactly the intent the old `sent` counter carried there.

Anything that used to assert on a captured SMTP call now drains with the
`outbox_drain` fixture and asserts on what the drain handed SMTP.
"""
from database import api_tx
import emails.send_community_weekly as e2
from service.campaigns import log_send
from service.campaigns.runner import run_campaign
from service.unsubscribe import stamp_unsubscribed


# ---------------------------------------------------------------------------
# Final review, item 4: the weekly email's own cap window is 6 days -- the
# admin send endpoint reads it via getattr(mod, 'CAP_DAYS', 7)
# (service/api/admin/growth_routes.py), so a regression here would silently
# fall back to the default 7-day cap.
# ---------------------------------------------------------------------------

def test_send_community_weekly_cap_days_is_six():
    assert e2.CAP_DAYS == 6


def _outbox_count(person_id: int) -> int:
    with api_tx('read committed') as tx:
        return tx.execute("SELECT count(*) AS n FROM email_outbox WHERE person_id = %(id)s",
                          dict(id=person_id)).fetchone()['n']


def test_runner_dry_run_queues_nothing_and_logs_nothing(make_person):
    p = make_person(name='Dry')
    # NOTE: not the person's real (fixture) email: make_person uses
    # @example.com, which emails.base.is_suppressed_send blocks by default
    # (a deliberate no-send safety net for QA/doc-reserved domains). The
    # runner rightly checks that suppression list, so a suppressed address
    # would make built == 0 for reasons unrelated to what this test verifies
    # (dry-run behaviour). Use a reserved-but-unsuppressed domain instead.
    email = f"runner-dry-{p['id']}@ahavah-test.invalid"
    res = run_campaign(api_tx, 'e1', 'run-dry', [dict(person_id=p['id'], email=email, name='Dry')],
                       lambda row: ('Subj', '<p>hi</p>'), send=False, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['dry_run'] and res['built'] == 1 and res['queued'] == 0
    assert _outbox_count(p['id']) == 0
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT count(*) AS n FROM email_send_log WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['n'] == 0


def test_runner_queues_once_per_campaign_id(make_person, outbox_drain):
    p = make_person(name='Once')
    # See note above: avoid make_person's suppressed @example.com domain.
    email = f"runner-once-{p['id']}@ahavah-test.invalid"
    rows = [dict(person_id=p['id'], email=email, name='Once')]
    a = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    b = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert a['queued'] == 1 and b['queued'] == 0 and _outbox_count(p['id']) == 1
    sent = outbox_drain(p['id'])
    assert len(sent) == 1 and sent[0]['to_addr'] == email


def test_runner_stops_and_reports_on_build_failure_mid_run(make_person, outbox_drain):
    """A run that cannot build one recipient's message stops there and
    reports the partial result, leaving the recipients it already handled
    queued (and therefore, after a drain, logged).

    This was the SMTP-failure test before the outbox landed. A failed SEND is
    no longer the runner's business at all -- it is retried and, after
    MAX_ATTEMPTS, parked in 'failed' by the drain (see
    tests/test_email_outbox.py::test_smtp_failure_retries_then_fails) -- so
    the stop-and-report behaviour is exercised on the one failure the runner
    still owns."""
    p1 = make_person(name='FailFirst')
    p2 = make_person(name='FailSecond')
    email1 = f"runner-fail-1-{p1['id']}@ahavah-test.invalid"
    email2 = f"runner-fail-2-{p2['id']}@ahavah-test.invalid"
    rows = [dict(person_id=p1['id'], email=email1, name='FailFirst'),
            dict(person_id=p2['id'], email=email2, name='FailSecond')]

    def build(row):
        if row['name'] == 'FailSecond':
            raise RuntimeError('boom')
        return ('S', '<p>x</p>')

    res = run_campaign(api_tx, 'e1', 'run-fail', rows, build, send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['queued'] == 1
    assert res['error'] == 'boom'
    from emails.base import mask_email
    assert res['failed_email'] == mask_email(email2)
    with api_tx('read committed') as tx:
        queued = tx.execute(
            "SELECT person_id FROM email_outbox WHERE campaign = 'e1' AND campaign_id = 'run-fail'").fetchall()
    assert [r['person_id'] for r in queued] == [p1['id']]

    outbox_drain(p1['id'], p2['id'])
    with api_tx('read committed') as tx:
        logged = tx.execute(
            "SELECT person_id FROM email_send_log WHERE campaign = 'e1' AND campaign_id = 'run-fail'").fetchall()
    assert [r['person_id'] for r in logged] == [p1['id']]


def _set_email(person_id: int, email: str) -> str:
    """make_person hands out an @example.com address, which
    emails.base.is_suppressed_send blocks. Give the row a reserved but
    unsuppressed address so the runner reaches the checks under test."""
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s, normalized_email = %(e)s WHERE id = %(i)s",
                   dict(e=email, i=person_id))
    return email


# ---------------------------------------------------------------------------
# I7: a dry run builds every message, so a build that raises must be reported
# the same way a failed enqueue is, not crash the caller.
# ---------------------------------------------------------------------------

def test_dry_run_build_failure_returns_the_partial_result(make_person):
    p1 = make_person(name='BuildOk')
    p2 = make_person(name='BuildBoom')
    rows = [dict(person_id=p1['id'], email=f"build-ok-{p1['id']}@ahavah-test.invalid", name='BuildOk'),
            dict(person_id=p2['id'], email=f"build-boom-{p2['id']}@ahavah-test.invalid", name='BuildBoom')]

    def build(row):
        if row['name'] == 'BuildBoom':
            raise RuntimeError('bad template')
        return ('S', '<p>x</p>')

    res = run_campaign(api_tx, 'e1', 'dry-build-fail', rows, build, send=False,
                       from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['dry_run'] is True
    assert res['built'] == 1
    assert res['error'] == 'bad template'
    from emails.base import mask_email
    assert res['failed_email'] == mask_email(rows[1]['email'])


# ---------------------------------------------------------------------------
# I4: a footer unsubscribe must suppress the NEXT campaign in that scope.
# ---------------------------------------------------------------------------

def test_notifications_unsubscribe_skips_an_e1_run(make_person):
    p = make_person(name='UnsubNotif')
    email = _set_email(p['id'], f"unsub-notif-{p['id']}@ahavah-test.invalid")
    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'notifications', email)
    res = run_campaign(api_tx, 'e1', 'unsub-e1', [dict(person_id=p['id'], email=email, name='UnsubNotif')],
                       lambda row: ('S', '<p>x</p>'), send=False,
                       from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['built'] == 0 and res['skipped_unsubscribed'] == 1


def test_community_unsubscribe_skips_e2_but_not_e1(make_person):
    p = make_person(name='UnsubCommunity')
    email = _set_email(p['id'], f"unsub-community-{p['id']}@ahavah-test.invalid")
    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'community', email)
    rows = [dict(person_id=p['id'], email=email, name='UnsubCommunity')]
    e2res = run_campaign(api_tx, 'e2', 'unsub-e2', rows, lambda row: ('S', '<p>x</p>'), send=False,
                         from_addr='support@ahavah.app', unsub_scope='community')
    assert e2res['built'] == 0 and e2res['skipped_unsubscribed'] == 1
    e1res = run_campaign(api_tx, 'e1', 'unsub-e1-after-community', rows, lambda row: ('S', '<p>x</p>'),
                         send=False, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert e1res['built'] == 1 and e1res['skipped_unsubscribed'] == 0


# ---------------------------------------------------------------------------
# I8: the weekly email runs weekly, so its cap window is 6 days -- a member
# mailed 6.5 days ago is due again, one mailed 5 days ago is not.
# ---------------------------------------------------------------------------

def _log_e2_days_ago(person_id: int, campaign_id: str, days: float) -> None:
    with api_tx() as tx:
        log_send(tx, person_id, 'e2', campaign_id, 'mid')
        tx.execute("UPDATE email_send_log SET sent_at = NOW() - make_interval(secs => %(s)s) "
                   "WHERE person_id = %(i)s AND campaign_id = %(c)s",
                   dict(s=days * 86400, i=person_id, c=campaign_id))


def test_weekly_cap_of_six_days_lets_the_next_weekly_run_through(make_person):
    due = make_person(name='WeeklyDue')
    early = make_person(name='WeeklyEarly')
    due_email = f"weekly-due-{due['id']}@ahavah-test.invalid"
    early_email = f"weekly-early-{early['id']}@ahavah-test.invalid"
    _log_e2_days_ago(due['id'], 'e2-last-week', 6.5)
    _log_e2_days_ago(early['id'], 'e2-midweek', 5)

    res = run_campaign(api_tx, 'e2', 'e2-this-week',
                       [dict(person_id=due['id'], email=due_email, name='WeeklyDue'),
                        dict(person_id=early['id'], email=early_email, name='WeeklyEarly')],
                       lambda row: ('S', '<p>x</p>'), send=False, from_addr='support@ahavah.app',
                       unsub_scope='community', cap_days=6)
    assert res['built'] == 1 and res['skipped_cap'] == 1


def test_the_six_day_cap_survives_the_drains_own_recheck(make_person, outbox_drain):
    """The drain re-checks the frequency cap at SEND time, and it has to do
    so with the campaign's OWN window. A member mailed 6.5 days ago passes
    E2's 6-day cap at enqueue; re-checking with the 7-day default seconds
    later would then skip the very member the 6-day window exists to reach."""
    due = make_person(name='WeeklyDrain')
    due_email = _set_email(due['id'], f"weekly-drain-{due['id']}@ahavah-test.invalid")
    _log_e2_days_ago(due['id'], 'e2-drain-last-week', 6.5)

    res = run_campaign(api_tx, 'e2', 'e2-drain-this-week',
                       [dict(person_id=due['id'], email=due_email, name='WeeklyDrain')],
                       lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app',
                       unsub_scope='community', cap_days=6)
    assert res['queued'] == 1
    sent = outbox_drain(due['id'])
    assert len(sent) == 1 and sent[0]['to_addr'] == due_email


def test_a_rerun_logs_already_queued_not_queued(make_person, capsys):
    """Fix round 1, ruling 3. A second run of the same campaign_id adds no
    rows -- that is the point of enqueue's unique key -- so its log must not
    read like a second send went out."""
    p = make_person(name='LogOnce')
    email = f"log-once-{p['id']}@ahavah-test.invalid"
    rows = [dict(person_id=p['id'], email=email, name='LogOnce')]
    kwargs = dict(send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    run_campaign(api_tx, 'e1', 'log-once-1', rows, lambda row: ('S', '<p>x</p>'), **kwargs)
    first = capsys.readouterr().out
    run_campaign(api_tx, 'e1', 'log-once-1', rows, lambda row: ('S', '<p>x</p>'), **kwargs)
    second = capsys.readouterr().out
    assert 'queued e1' in first and 'already queued e1' not in first
    assert 'already queued e1' in second
