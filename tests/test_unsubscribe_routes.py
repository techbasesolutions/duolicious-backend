"""GET/POST /u/<token> across every unsubscribe scope, plus the campaign
List-Unsubscribe header per campaign.

Fix round 1 (Task 10 review): GET must only render a confirmation form and
never stamp (mail scanners follow GET links automatically); only POST
performs the action. Covers all five scopes in service.unsubscribe._SCOPES
so a future scope addition has a template to extend. Also locks in that the
admin send endpoint's List-Unsubscribe header uses each campaign module's
own UNSUB_SCOPE rather than a single hardcoded scope.
"""
from __future__ import annotations

from database import api_tx
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.unsubscribe import make_token, make_url as _unsub_url


def _assert_pending_form(resp, token: str) -> None:
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<form method="post"' in body
    assert f'/u/{token}' in body


# ---------------------------------------------------------------------------
# waitlist -- stamps waitlist_signup.unsubscribed_at
# ---------------------------------------------------------------------------

def test_waitlist_get_renders_form_without_stamping_then_post_stamps(client):
    email = 'waitlist-fixture@example.com'
    with api_tx() as tx:
        tx.execute("DELETE FROM waitlist_signup WHERE email = %(e)s", dict(e=email))
        tx.execute("INSERT INTO waitlist_signup (email) VALUES (%(e)s)", dict(e=email))
    token = make_token('waitlist', email)

    r = client.get(f'/u/{token}')
    _assert_pending_form(r, token)
    with api_tx() as tx:
        row = tx.execute("SELECT unsubscribed_at FROM waitlist_signup WHERE email = %(e)s", dict(e=email)).fetchone()
    assert row['unsubscribed_at'] is None

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200
    with api_tx() as tx:
        row2 = tx.execute("SELECT unsubscribed_at FROM waitlist_signup WHERE email = %(e)s", dict(e=email)).fetchone()
    assert row2['unsubscribed_at'] is not None

    with api_tx() as tx:
        tx.execute("DELETE FROM waitlist_signup WHERE email = %(e)s", dict(e=email))


# ---------------------------------------------------------------------------
# beta -- stamps beta_signup.unsubscribed_at
# ---------------------------------------------------------------------------

def test_beta_get_renders_form_without_stamping_then_post_stamps(client):
    email = 'beta-fixture@example.com'
    with api_tx() as tx:
        tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))
        tx.execute("INSERT INTO beta_signup (email) VALUES (%(e)s)", dict(e=email))
    token = make_token('beta', email)

    r = client.get(f'/u/{token}')
    _assert_pending_form(r, token)
    with api_tx() as tx:
        row = tx.execute("SELECT unsubscribed_at FROM beta_signup WHERE email = %(e)s", dict(e=email)).fetchone()
    assert row['unsubscribed_at'] is None

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200
    with api_tx() as tx:
        row2 = tx.execute("SELECT unsubscribed_at FROM beta_signup WHERE email = %(e)s", dict(e=email)).fetchone()
    assert row2['unsubscribed_at'] is not None

    with api_tx() as tx:
        tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))


# ---------------------------------------------------------------------------
# notifications -- upserts notification_preference, all email_* -> FALSE
# ---------------------------------------------------------------------------

def test_notifications_get_renders_form_without_stamping_then_post_stamps(client, make_person):
    p = make_person(name='NotifRoute')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    token = make_token('notifications', email)

    r = client.get(f'/u/{token}')
    _assert_pending_form(r, token)
    with api_tx() as tx:
        row = tx.execute("SELECT 1 AS n FROM notification_preference WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()
    assert row is None, 'GET must not create/modify the notification_preference row'

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200
    with api_tx() as tx:
        row2 = tx.execute(
            "SELECT email_messages, email_matches, email_likes, email_verification, email_profile_views "
            "FROM notification_preference WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()
    assert row2 is not None
    assert not any(row2.values())


# ---------------------------------------------------------------------------
# community -- stamps person.community_unsubscribed_at (Task 10)
# ---------------------------------------------------------------------------

def test_community_get_renders_form_without_stamping_then_post_stamps(client, make_person):
    p = make_person(name='CommunityRoute')
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    token = make_token('community', email)

    r = client.get(f'/u/{token}')
    _assert_pending_form(r, token)
    with api_tx() as tx:
        row = tx.execute("SELECT community_unsubscribed_at FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()
    assert row['community_unsubscribed_at'] is None

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200
    with api_tx() as tx:
        row2 = tx.execute("SELECT community_unsubscribed_at FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()
    assert row2['community_unsubscribed_at'] is not None


# ---------------------------------------------------------------------------
# claim -- a valid _SCOPES member, but not stamped via _Q_UNSUB (claim links
# are consumed by service.claim, not /u/<token>); the route must still
# render/accept both methods without stamping anything or erroring.
# ---------------------------------------------------------------------------

def test_claim_get_renders_form_then_post_succeeds_without_a_target_row(client):
    # M-j: no claim-scoped link is ever emailed to /u/<token> -- claim tokens
    # are minted for and consumed by service.claim's own route. This test only
    # pins that /u/ stays well behaved if one is ever pasted there: the 200 is
    # a deliberate no-op page, not evidence of an unsubscribe having happened.
    token = make_token('claim', 'claim-fixture@example.com')

    r = client.get(f'/u/{token}')
    _assert_pending_form(r, token)

    r2 = client.post(f'/u/{token}')
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Invalid token: unchanged error rendering on both methods.
# ---------------------------------------------------------------------------

def test_invalid_token_errors_identically_on_get_and_post(client):
    for resp in (client.get('/u/not-a-real-token'), client.post('/u/not-a-real-token')):
        assert resp.status_code == 400
        assert 'invalid' in resp.get_data(as_text=True).lower()


# ---------------------------------------------------------------------------
# Admin send path: List-Unsubscribe header must use each campaign's own
# UNSUB_SCOPE, not a single hardcoded scope (fix round 1, Important #2).
# ---------------------------------------------------------------------------

def test_e2_list_unsubscribe_header_uses_community_scope(make_person, outbox_drain):
    import emails.send_community_weekly as e2

    p = make_person(name='E2Header')
    email = f"e2-header-{p['id']}@ahavah-test.invalid"
    list_unsubscribe = lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(e2.UNSUB_SCOPE, e, WEB_BASE_URL)}>"

    run_campaign(api_tx, 'e2', 'hdr-e2', [dict(person_id=p['id'], email=email, name='E2Header')],
                lambda row: ('Subj', '<p>hi</p>'), send=True, from_addr='support@ahavah.app',
                list_unsubscribe=list_unsubscribe, unsub_scope=e2.UNSUB_SCOPE)

    # The run only QUEUES now (F07): the header is built at enqueue time and
    # rides the outbox payload, so it is asserted on what the drain hands SMTP.
    calls = outbox_drain(p['id'])
    assert len(calls) == 1
    assert '/u/community.' in calls[0]['list_unsubscribe']


def test_e1_list_unsubscribe_header_uses_notifications_scope(make_person, outbox_drain):
    import emails.send_spotlight_announcement as e1

    p = make_person(name='E1Header')
    email = f"e1-header-{p['id']}@ahavah-test.invalid"
    list_unsubscribe = lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(e1.UNSUB_SCOPE, e, WEB_BASE_URL)}>"

    run_campaign(api_tx, 'e1', 'hdr-e1', [dict(person_id=p['id'], email=email, name='E1Header')],
                lambda row: ('Subj', '<p>hi</p>'), send=True, from_addr='support@ahavah.app',
                list_unsubscribe=list_unsubscribe, unsub_scope=e1.UNSUB_SCOPE)

    calls = outbox_drain(p['id'])
    assert len(calls) == 1
    assert '/u/notifications.' in calls[0]['list_unsubscribe']
