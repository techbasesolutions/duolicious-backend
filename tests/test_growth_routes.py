"""The /admin/growth/* endpoints, exercised through the real Flask client.

Everything below the route (the runner, the campaign modules, the
unsubscribe scopes) has its own unit tests. This file covers the wiring the
unit tests cannot see: the admin gate, the 404/400 argument handling, the
audit row the send endpoint writes, and the List-Unsubscribe header that
reaches SMTP for each campaign's own scope.

No admin-session fixture existed before this file, so it mints one the same
way tests/test_checkout.py mints a member session (a real `duo_session` row
+ the bearer header `require_auth` reads), plus the `admin` role that
`service.admin.queries.Q_IS_ADMIN` checks for.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

import pytest

from database import api_tx

import emails.send_community_weekly as e2
import emails.send_reinvite as e3
import emails.send_spotlight_announcement as e1


def _bearer(person_id: int, email: str) -> dict:
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')
            """,
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=person_id),
        )
    return {'Authorization': f'Bearer {tok}'}


def _email_of(person_id: int) -> str:
    with api_tx('read committed') as tx:
        return tx.execute("SELECT email FROM person WHERE id = %(i)s",
                          dict(i=person_id)).fetchone()['email']


def _sendable_email(person_id: int, label: str) -> str:
    """make_person hands out an @example.com address, which
    emails.base.is_suppressed_send blocks by design. Swap in a reserved but
    unsuppressed address so a send test actually reaches SMTP."""
    email = f'{label}-{person_id}@ahavah-test.invalid'
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s, normalized_email = %(e)s WHERE id = %(i)s",
                   dict(e=email, i=person_id))
    return email


@pytest.fixture
def member(make_person):
    """A signed-in member with no admin role."""
    p = make_person(name='GrowthMember')
    return dict(id=p['id'], uuid=p['uuid'], headers=_bearer(p['id'], _email_of(p['id'])))


@pytest.fixture
def admin(make_person):
    p = make_person(name='GrowthAdmin')
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s",
                   dict(i=p['id']))
    return dict(id=p['id'], uuid=p['uuid'], headers=_bearer(p['id'], _email_of(p['id'])))


class _CapturingSmtp:
    def __init__(self):
        self.calls = []

    def send(self, **kw):
        self.calls.append(kw)
        return f'mid-{len(self.calls)}'


# ---------------------------------------------------------------------------
# Admin gate
# ---------------------------------------------------------------------------

def test_non_admin_is_refused_on_every_growth_endpoint(client, member):
    assert client.get('/admin/growth/emails', headers=member['headers']).status_code == 403
    assert client.get('/admin/growth/stats', headers=member['headers']).status_code == 403
    r = client.post('/admin/growth/emails/e1/send', headers=member['headers'],
                    json=dict(campaign_id='nope', dry_run=True))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------

def test_unknown_campaign_is_404(client, admin):
    r = client.post('/admin/growth/emails/e9/send', headers=admin['headers'],
                    json=dict(campaign_id='x', dry_run=True))
    assert r.status_code == 404


def test_missing_campaign_id_is_400(client, admin):
    r = client.post('/admin/growth/emails/e1/send', headers=admin['headers'], json={})
    assert r.status_code == 400


def test_preview_rejects_a_malformed_recipient(client, admin, monkeypatch):
    """M-h: the preview endpoint mails whatever `to` it is handed, so a
    non-address must be refused before SMTP is touched."""
    smtp = _CapturingSmtp()
    monkeypatch.setattr('smtp.make_aws_smtp', lambda: smtp)

    for bad in ('not-an-email', 'a@b', 'a b@c.co', '', 'a@@b.co'):
        r = client.post('/admin/growth/emails/e1/preview', headers=admin['headers'],
                        json=dict(to=bad))
        assert r.status_code == 400, f'{bad!r} should be refused'
    assert smtp.calls == []


# ---------------------------------------------------------------------------
# Send endpoint: result dict + exactly one audit row (C1 -- the audit call
# used to raise TypeError on duplicate `campaign_id`/`dry_run` keys, so a dry
# run 500'd after the campaign had already been built).
# ---------------------------------------------------------------------------

def test_dry_run_send_returns_the_result_and_writes_one_audit_row(client, admin, make_person, monkeypatch):
    p = make_person(name='DryTarget')
    email = _sendable_email(p['id'], 'growth-dry')
    monkeypatch.setattr(e1, 'recipients',
                        lambda: [dict(person_id=p['id'], email=email, name='DryTarget')])
    cid = f'e1-dry-{uuid.uuid4().hex[:8]}'

    r = client.post('/admin/growth/emails/e1/send', headers=admin['headers'],
                    json=dict(campaign_id=cid, dry_run=True))
    assert r.status_code == 200
    body = r.get_json()
    assert body['dry_run'] is True
    assert body['campaign_id'] == cid
    # A dry run builds every message and queues none (F07), so `built` is
    # what carries the "one recipient made it through every check" intent
    # the old `sent` counter carried here.
    assert body['built'] == 1
    assert body['queued'] == 0
    assert body['error'] is None

    with api_tx('read committed') as tx:
        rows = tx.execute(
            "SELECT metadata FROM admin_audit_log "
            " WHERE action = 'growth.email.send' AND metadata->>'campaign_id' = %(c)s",
            dict(c=cid)).fetchall()
    assert len(rows) == 1
    assert rows[0]['metadata']['campaign'] == 'e1'
    assert rows[0]['metadata']['dry_run'] is True
    assert rows[0]['metadata']['built'] == 1


def test_e1_send_sets_the_notifications_list_unsubscribe_header(client, admin, make_person,
                                                                monkeypatch, outbox_drain):
    """The header is built at enqueue time and rides the outbox payload, so
    it is asserted on what the DRAIN hands SMTP -- the send endpoint itself
    no longer touches SMTP at all (F07)."""
    p = make_person(name='E1Target')
    email = _sendable_email(p['id'], 'growth-e1')
    monkeypatch.setattr(e1, 'recipients',
                        lambda: [dict(person_id=p['id'], email=email, name='E1Target')])

    r = client.post('/admin/growth/emails/e1/send', headers=admin['headers'],
                    json=dict(campaign_id=f'e1-real-{uuid.uuid4().hex[:8]}', dry_run=False))
    assert r.status_code == 200 and r.get_json()['queued'] == 1
    sent = outbox_drain(p['id'])
    assert len(sent) == 1
    assert '/u/notifications.' in sent[0]['list_unsubscribe']


def test_e2_send_sets_the_community_list_unsubscribe_header(client, admin, make_person,
                                                            monkeypatch, outbox_drain):
    p = make_person(name='E2Target')
    email = _sendable_email(p['id'], 'growth-e2')
    # preview_row() carries the per-run week context that build_for() reads.
    monkeypatch.setattr(e2, 'recipients',
                        lambda: [dict(e2.preview_row(email), person_id=p['id'], name='E2Target')])

    r = client.post('/admin/growth/emails/e2/send', headers=admin['headers'],
                    json=dict(campaign_id=f'e2-real-{uuid.uuid4().hex[:8]}', dry_run=False))
    assert r.status_code == 200 and r.get_json()['queued'] == 1
    sent = outbox_drain(p['id'])
    assert len(sent) == 1
    assert '/u/community.' in sent[0]['list_unsubscribe']


def test_send_endpoint_is_idempotent_and_status_reports_the_run(client, admin, make_person,
                                                                monkeypatch, outbox_drain):
    """An admin who clicks send twice queues one message, not two, and the
    status endpoint reports where that run got to (F07/F08)."""
    p = make_person(name='StatusTarget')
    email = _sendable_email(p['id'], 'growth-status')
    monkeypatch.setattr(e1, 'recipients',
                        lambda: [dict(person_id=p['id'], email=email, name='StatusTarget')])
    cid = f'e1-status-{uuid.uuid4().hex[:8]}'

    first = client.post('/admin/growth/emails/e1/send', headers=admin['headers'],
                        json=dict(campaign_id=cid, dry_run=False))
    second = client.post('/admin/growth/emails/e1/send', headers=admin['headers'],
                         json=dict(campaign_id=cid, dry_run=False))
    assert first.get_json()['queued'] == 1 and second.get_json()['queued'] == 0

    r = client.get(f'/admin/growth/emails/e1/status/{cid}', headers=admin['headers'])
    assert r.status_code == 200
    assert r.get_json() == dict(queued=1, reserved=0, accepted=0, acceptance_unknown=0,
                                failed=0, skipped=0)

    assert len(outbox_drain(p['id'])) == 1
    after = client.get(f'/admin/growth/emails/e1/status/{cid}', headers=admin['headers']).get_json()
    assert after == dict(queued=0, reserved=0, accepted=1, acceptance_unknown=0,
                         failed=0, skipped=0)
    assert client.get(f'/admin/growth/emails/nope/status/{cid}',
                      headers=admin['headers']).status_code == 404


# ---------------------------------------------------------------------------
# I6: the index must count recipients, not materialise them.
# ---------------------------------------------------------------------------

def test_emails_index_lists_five_campaigns_with_integer_counts(client, admin):
    r = client.get('/admin/growth/emails', headers=admin['headers'])
    assert r.status_code == 200
    campaigns = r.get_json()['campaigns']
    assert {c['campaign'] for c in campaigns} == {'e1', 'e2', 'e3', 'e4', 'e5'}
    for c in campaigns:
        assert isinstance(c['recipients'], int)
        assert c['recipients'] >= 0


def test_emails_index_lists_system_sent_campaigns(client, admin, make_person):
    """e4 (the member invite) and e5 (the card-went-live receipt) are sent
    by the platform itself, not run from this admin screen: they carry
    system=True and their recipient count comes straight from
    email_send_log rather than a campaign module's recipients()."""
    p = make_person(name='SystemCampaignTarget')
    with api_tx() as tx:
        tx.execute(
            "INSERT INTO email_send_log (person_id, campaign, campaign_id, sent_at) "
            "VALUES (%(p)s, 'e4', 'e4-rk1', NOW())", dict(p=p['id']))
    out = client.get('/admin/growth/emails', headers=admin['headers']).get_json()['campaigns']
    keys = [c['campaign'] for c in out]
    assert keys == ['e1', 'e2', 'e3', 'e4', 'e5']
    e4 = next(c for c in out if c['campaign'] == 'e4')
    assert e4['system'] is True and e4['recipients'] == 1 and e4['last_campaign_id'] == 'e4-rk1'
    assert next(c for c in out if c['campaign'] == 'e1')['system'] is False


def test_recipient_count_agrees_with_the_recipient_list(make_person):
    make_person(name='CountSeedA', gender='Man')
    make_person(name='CountSeedB', gender='Woman')
    for mod in (e1, e2, e3):
        assert mod.recipient_count() == len(mod.recipients())


def test_every_campaign_module_exposes_a_recipient_count():
    for mod in (e1, e2, e3):
        assert isinstance(mod.recipient_count(), int)


# ---------------------------------------------------------------------------
# Final review, item 3: recipient_count() (the admin index number) must
# apply the same suppression + scope-unsubscribe filters run_campaign()
# applies per-row, so a member who has unsubscribed from a campaign's own
# scope no longer inflates the count.
# ---------------------------------------------------------------------------

def test_notifications_unsubscribe_reduces_e1_recipient_count(make_person):
    from service.unsubscribe import stamp_unsubscribed
    p = make_person(name='UnsubCountE1')
    email = _sendable_email(p['id'], 'unsub-count-e1')
    before = e1.recipient_count()
    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'notifications', email)
    assert e1.recipient_count() == before - 1
    assert e1.recipient_count() == len(e1.recipients())


def test_community_unsubscribe_reduces_e2_recipient_count(make_person):
    from service.unsubscribe import stamp_unsubscribed
    p = make_person(name='UnsubCountE2')
    email = _sendable_email(p['id'], 'unsub-count-e2')
    before = e2.recipient_count()
    with api_tx() as tx:
        assert stamp_unsubscribed(tx, 'community', email)
    assert e2.recipient_count() == before - 1
    assert e2.recipient_count() == len(e2.recipients())


def test_suppressed_domain_reduces_e1_recipient_count(make_person):
    """A suppressed-domain address (emails.base's default list includes
    techbaseltd.com) must not count towards the admin index number, since
    run_campaign() would skip it as `skipped_suppressed` at send time."""
    p = make_person(name='SuppressedCountE1')
    email = _sendable_email(p['id'], 'presuppress-e1')  # start on an unsuppressed domain
    before = e1.recipient_count()
    assert before == len(e1.recipients())
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s, normalized_email = %(e)s WHERE id = %(i)s",
                   dict(e=f"suppressed-{p['id']}@techbaseltd.com", i=p['id']))
    assert e1.recipient_count() == before - 1
    assert e1.recipient_count() == len(e1.recipients())


# ---------------------------------------------------------------------------
# Final review, item 4: the weekly email's cap window is 6 days, not the
# default 7 -- asserted directly on the module constant the admin send
# endpoint reads via getattr(mod, 'CAP_DAYS', 7).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task 7: the cross-campaign view of F08's acceptance_unknown mail. The
# per-run status endpoint above requires the operator to already know the
# campaign_id they are looking for; this is the index that lets them find it.
# ---------------------------------------------------------------------------

def test_unknown_mail_summary_lists_every_run_with_acceptance_unknown_rows(client, admin, make_person):
    p = make_person(name='UnknownMailTarget')
    email = _sendable_email(p['id'], 'unknown-mail')
    cid = f'e1-unknown-{uuid.uuid4().hex[:8]}'
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO email_outbox (campaign, campaign_id, person_id, email, payload,
                                          unsub_scope, state, reserved_at)
               VALUES ('e1', %(cid)s, %(pid)s, %(email)s, '{}'::jsonb, 'notifications',
                       'acceptance_unknown', NOW())""",
            dict(cid=cid, pid=p['id'], email=email))
    r = client.get('/admin/growth/emails/unknown', headers=admin['headers'])
    assert r.status_code == 200
    rows = r.get_json()
    mine = next((row for row in rows if row['campaign_id'] == cid), None)
    assert mine is not None
    assert mine['campaign'] == 'e1'
    assert mine['n'] == 1


def test_unknown_mail_summary_omits_runs_with_no_unknown_rows(client, admin, make_person):
    p = make_person(name='ResolvedMailTarget')
    email = _sendable_email(p['id'], 'resolved-mail')
    cid = f'e1-resolved-{uuid.uuid4().hex[:8]}'
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO email_outbox (campaign, campaign_id, person_id, email, payload,
                                          unsub_scope, state, sent_at)
               VALUES ('e1', %(cid)s, %(pid)s, %(email)s, '{}'::jsonb, 'notifications',
                       'accepted', NOW())""",
            dict(cid=cid, pid=p['id'], email=email))
    rows = client.get('/admin/growth/emails/unknown', headers=admin['headers']).get_json()
    assert not any(row['campaign_id'] == cid for row in rows)


def test_unknown_mail_summary_requires_admin(client, member):
    assert client.get('/admin/growth/emails/unknown', headers=member['headers']).status_code == 403


def test_e2_dry_run_send_endpoint_respects_the_six_day_cap(client, admin, make_person, monkeypatch):
    """Through the real admin send endpoint (not run_campaign() directly):
    a member mailed 6.5 days ago is due again under the 6-day weekly cap,
    one mailed 5 days ago is not."""
    from service.campaigns import log_send

    due = make_person(name='WeeklyDueEndpoint')
    early = make_person(name='WeeklyEarlyEndpoint')
    due_email = _sendable_email(due['id'], 'weekly-due-ep')
    early_email = _sendable_email(early['id'], 'weekly-early-ep')

    def _log_days_ago(person_id: int, campaign_id: str, days: float) -> None:
        with api_tx() as tx:
            log_send(tx, person_id, 'e2', campaign_id, 'mid')
            tx.execute(
                "UPDATE email_send_log SET sent_at = NOW() - make_interval(secs => %(s)s) "
                "WHERE person_id = %(i)s AND campaign_id = %(c)s",
                dict(s=days * 86400, i=person_id, c=campaign_id))

    _log_days_ago(due['id'], 'e2-ep-last-week', 6.5)
    _log_days_ago(early['id'], 'e2-ep-midweek', 5)

    rows = [dict(e2.preview_row(due_email), person_id=due['id'], name='WeeklyDueEndpoint'),
            dict(e2.preview_row(early_email), person_id=early['id'], name='WeeklyEarlyEndpoint')]
    monkeypatch.setattr(e2, 'recipients', lambda: rows)

    r = client.post('/admin/growth/emails/e2/send', headers=admin['headers'],
                    json=dict(campaign_id=f'e2-cap-ep-{uuid.uuid4().hex[:8]}', dry_run=True))

    assert r.status_code == 200
    body = r.get_json()
    assert body['built'] == 1
    assert body['skipped_cap'] == 1
