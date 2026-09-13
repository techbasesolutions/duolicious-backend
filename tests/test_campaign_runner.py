from database import api_tx
from service.campaigns import log_send
from service.campaigns.runner import run_campaign
from service.unsubscribe import stamp_unsubscribed

class _Smtp:
    def __init__(self): self.sent = []
    def send(self, **kw): self.sent.append(kw['to_addr']); return 'mid-' + str(len(self.sent))

def test_runner_dry_run_sends_nothing_and_logs_nothing(make_person, monkeypatch):
    import service.campaigns.runner as r
    smtp = _Smtp(); monkeypatch.setattr(r, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='Dry')
    # NOTE: not the person's real (fixture) email: make_person uses
    # @example.com, which emails.base.is_suppressed_send blocks by default
    # (a deliberate no-send safety net for QA/doc-reserved domains). The
    # runner rightly checks that suppression list, so a suppressed address
    # would make sent == 0 for reasons unrelated to what this test verifies
    # (dry-run behaviour). Use a reserved-but-unsuppressed domain instead.
    email = f"runner-dry-{p['id']}@ahavah-test.invalid"
    res = run_campaign(api_tx, 'e1', 'run-dry', [dict(person_id=p['id'], email=email, name='Dry')],
                       lambda row: ('Subj', '<p>hi</p>'), send=False, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['dry_run'] and res['sent'] == 1 and smtp.sent == []
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT count(*) AS n FROM email_send_log WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['n'] == 0

def test_runner_sends_once_per_campaign_id(make_person, monkeypatch):
    import service.campaigns.runner as r
    smtp = _Smtp(); monkeypatch.setattr(r, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='Once')
    # See note above: avoid make_person's suppressed @example.com domain.
    email = f"runner-once-{p['id']}@ahavah-test.invalid"
    rows = [dict(person_id=p['id'], email=email, name='Once')]
    a = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    b = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert a['sent'] == 1 and b['sent'] == 0 and len(smtp.sent) == 1

class _FailingSmtp:
    def __init__(self): self.sent = []
    def send(self, **kw):
        if len(self.sent) == 1:
            raise RuntimeError('boom')
        self.sent.append(kw['to_addr']); return 'mid-' + str(len(self.sent))

def test_runner_stops_and_reports_on_send_failure(make_person, monkeypatch):
    import service.campaigns.runner as r
    smtp = _FailingSmtp(); monkeypatch.setattr(r, 'make_aws_smtp', lambda: smtp)
    p1 = make_person(name='FailFirst')
    p2 = make_person(name='FailSecond')
    email1 = f"runner-fail-1-{p1['id']}@ahavah-test.invalid"
    email2 = f"runner-fail-2-{p2['id']}@ahavah-test.invalid"
    rows = [dict(person_id=p1['id'], email=email1, name='FailFirst'),
            dict(person_id=p2['id'], email=email2, name='FailSecond')]
    res = run_campaign(api_tx, 'e1', 'run-fail', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert res['sent'] == 1
    assert res['error'] == 'boom'
    from emails.base import mask_email
    assert res['failed_email'] == mask_email(email2)
    with api_tx('read committed') as tx:
        n = tx.execute("SELECT count(*) AS n FROM email_send_log WHERE campaign = 'e1' AND campaign_id = 'run-fail'").fetchone()['n']
        assert n == 1
        logged = tx.execute("SELECT person_id FROM email_send_log WHERE campaign = 'e1' AND campaign_id = 'run-fail'").fetchone()
        assert logged['person_id'] == p1['id']


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
# the same way a failed send is, not crash the caller.
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
    assert res['sent'] == 1
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
    assert res['sent'] == 0 and res['skipped_unsubscribed'] == 1


def test_community_unsubscribe_skips_e2_but_not_e1(make_person):
    p = make_person(name='UnsubCommunity')
    email = _set_email(p['id'], f"unsub-community-{p['id']}@ahavah-test.invalid")
    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'community', email)
    rows = [dict(person_id=p['id'], email=email, name='UnsubCommunity')]
    e2 = run_campaign(api_tx, 'e2', 'unsub-e2', rows, lambda row: ('S', '<p>x</p>'), send=False,
                      from_addr='support@ahavah.app', unsub_scope='community')
    assert e2['sent'] == 0 and e2['skipped_unsubscribed'] == 1
    e1 = run_campaign(api_tx, 'e1', 'unsub-e1-after-community', rows, lambda row: ('S', '<p>x</p>'),
                      send=False, from_addr='support@ahavah.app', unsub_scope='notifications')
    assert e1['sent'] == 1 and e1['skipped_unsubscribed'] == 0


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
    assert res['sent'] == 1 and res['skipped_cap'] == 1
