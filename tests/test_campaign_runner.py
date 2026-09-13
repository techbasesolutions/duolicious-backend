from database import api_tx
from service.campaigns.runner import run_campaign

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
                       lambda row: ('Subj', '<p>hi</p>'), send=False, from_addr='support@ahavah.app')
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
    a = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app')
    b = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True, from_addr='support@ahavah.app')
    assert a['sent'] == 1 and b['sent'] == 0 and len(smtp.sent) == 1
