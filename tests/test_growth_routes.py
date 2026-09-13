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
    assert body['sent'] == 1
    assert body['error'] is None

    with api_tx('read committed') as tx:
        rows = tx.execute(
            "SELECT metadata FROM admin_audit_log "
            " WHERE action = 'growth.email.send' AND metadata->>'campaign_id' = %(c)s",
            dict(c=cid)).fetchall()
    assert len(rows) == 1
    assert rows[0]['metadata']['campaign'] == 'e1'
    assert rows[0]['metadata']['dry_run'] is True
    assert rows[0]['metadata']['sent'] == 1


def test_e1_send_sets_the_notifications_list_unsubscribe_header(client, admin, make_person, monkeypatch):
    import service.campaigns.runner as runner
    smtp = _CapturingSmtp()
    monkeypatch.setattr(runner, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='E1Target')
    email = _sendable_email(p['id'], 'growth-e1')
    monkeypatch.setattr(e1, 'recipients',
                        lambda: [dict(person_id=p['id'], email=email, name='E1Target')])

    r = client.post('/admin/growth/emails/e1/send', headers=admin['headers'],
                    json=dict(campaign_id=f'e1-real-{uuid.uuid4().hex[:8]}', dry_run=False))
    assert r.status_code == 200 and r.get_json()['sent'] == 1
    assert len(smtp.calls) == 1
    assert '/u/notifications.' in smtp.calls[0]['list_unsubscribe']


def test_e2_send_sets_the_community_list_unsubscribe_header(client, admin, make_person, monkeypatch):
    import service.campaigns.runner as runner
    smtp = _CapturingSmtp()
    monkeypatch.setattr(runner, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='E2Target')
    email = _sendable_email(p['id'], 'growth-e2')
    # preview_row() carries the per-run week context that build_for() reads.
    monkeypatch.setattr(e2, 'recipients',
                        lambda: [dict(e2.preview_row(email), person_id=p['id'], name='E2Target')])

    r = client.post('/admin/growth/emails/e2/send', headers=admin['headers'],
                    json=dict(campaign_id=f'e2-real-{uuid.uuid4().hex[:8]}', dry_run=False))
    assert r.status_code == 200 and r.get_json()['sent'] == 1
    assert len(smtp.calls) == 1
    assert '/u/community.' in smtp.calls[0]['list_unsubscribe']


# ---------------------------------------------------------------------------
# I6: the index must count recipients, not materialise them.
# ---------------------------------------------------------------------------

def test_emails_index_lists_three_campaigns_with_integer_counts(client, admin):
    r = client.get('/admin/growth/emails', headers=admin['headers'])
    assert r.status_code == 200
    campaigns = r.get_json()['campaigns']
    assert {c['campaign'] for c in campaigns} == {'e1', 'e2', 'e3'}
    for c in campaigns:
        assert isinstance(c['recipients'], int)
        assert c['recipients'] >= 0


def test_recipient_count_agrees_with_the_recipient_list(make_person):
    make_person(name='CountSeedA', gender='Man')
    make_person(name='CountSeedB', gender='Woman')
    assert e1.recipient_count() == len(e1.recipients())


def test_every_campaign_module_exposes_a_recipient_count():
    for mod in (e1, e2, e3):
        assert isinstance(mod.recipient_count(), int)
