"""
F19/F20/F21 - garbagerecords NSFW auto-delete visibility + beta re-opt-in.

F19: garbagerecords q7 hard-deletes photos with nsfw_score > 0.8. Before this
fix there was no staging into undeleted_photo (CDN leak, same class of bug as
F11) and no admin visibility, so a false positive on a 34-member faith
community's photo just silently vanished. The sweep now stages the uuid for
the CDN cleaner before deleting and emails the admin a per-photo summary
(person name + uuid + score) whenever any rows were removed.

F20: DUO_REPORT_EMAIL pointed at example.com in the repo .env.production copy
(config-only fix, no test - the file is gitignored and not imported by the
test suite).

F21: service/beta/__init__.py's `_Q_REGISTER` used to `ON CONFLICT DO
NOTHING`, so re-ticking the beta checkbox after unsubscribing was a silent
no-op (unsubscribed_at stayed set, the UI reported success, the member never
heard anything). `register()` now returns a (created, resubscribed) tuple
and both callers gate their welcome email on `created OR resubscribed`.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4
from unittest.mock import MagicMock

from database import api_tx
from service.beta import register


# ---------------------------------------------------------------------------
# F19: NSFW auto-delete staging + admin notice
# ---------------------------------------------------------------------------

def test_nsfw_delete_sql_stages_before_hard_delete():
    """Shape assertion: the nsfw_score > 0.8 hard-delete must be preceded by
    an undeleted_photo staging insert in the same CTE chain (F19)."""
    from service.cron.garbagerecords.sql import Q_DELETE_GARBAGE_RECORDS

    # The first `nsfw_score` occurrence is inside the staging CTE's WHERE
    # clause, which itself comes right after an `undeleted_photo` INSERT -
    # i.e. staging happens in the same CTE that filters on nsfw_score, not
    # bolted on somewhere unrelated.
    before_first_nsfw_score = Q_DELETE_GARBAGE_RECORDS.split('nsfw_score')[0]
    assert 'undeleted_photo' in before_first_nsfw_score, \
        'NSFW hard-delete must stage uuids for the CDN cleaner BEFORE the ' \
        'nsfw_score filter runs (F19)'
    assert 'ON CONFLICT DO NOTHING' in Q_DELETE_GARBAGE_RECORDS.split('nsfw_score')[1][:200], \
        'the staging insert must tolerate an already-staged uuid (F19, same as F11)'


def test_nsfw_delete_stages_undeleted_photo_and_notifies_admin(make_person, monkeypatch):
    """End-to-end: a photo with nsfw_score > 0.8 gets hard-deleted, its uuid
    lands in undeleted_photo for the CDN cleaner, and exactly one admin
    email goes out listing the person, uuid, and score (F19)."""
    from service.cron.garbagerecords import delete_garbage_records_once
    import service.cron.garbagerecords as garbagerecords_mod

    p = make_person(name='NsfwFalsePositive', gender='Woman')
    photo_uuid = f'nsfw-test-photo-{uuid4()}'

    with api_tx() as tx:
        tx.execute(
            "INSERT INTO photo (person_id, position, uuid, blurhash, hash, nsfw_score) "
            "VALUES (%(p)s, 1, %(uuid)s, '', %(uuid)s, 0.93)",
            dict(p=p['id'], uuid=photo_uuid),
        )

    mock_send = MagicMock(return_value=None)
    monkeypatch.setattr(garbagerecords_mod.aws_smtp, 'send', mock_send)

    try:
        asyncio.run(delete_garbage_records_once())

        with api_tx() as tx:
            photo_gone = tx.execute(
                "SELECT 1 FROM photo WHERE uuid = %(uuid)s", dict(uuid=photo_uuid)
            ).fetchone()
            assert photo_gone is None, 'photo with nsfw_score > 0.8 must be hard-deleted'

            staged = tx.execute(
                "SELECT 1 FROM undeleted_photo WHERE uuid = %(uuid)s", dict(uuid=photo_uuid)
            ).fetchone()
            assert staged, \
                'NSFW hard-delete must stage the uuid for the CDN cleaner (F19)'

        assert mock_send.call_count == 1, \
            'exactly one admin email must be sent when NSFW rows were removed (F19)'
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs['subject'] == 'NSFW auto-removal: 1 photo(s)'
        assert 'NsfwFalsePositive' in call_kwargs['body']
        assert photo_uuid in call_kwargs['body']
        assert '0.930' in call_kwargs['body'] or '0.93' in call_kwargs['body']
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM undeleted_photo WHERE uuid = %(uuid)s", dict(uuid=photo_uuid))
            tx.execute("DELETE FROM photo WHERE uuid = %(uuid)s", dict(uuid=photo_uuid))


def test_nsfw_sweep_with_nothing_to_remove_sends_no_email(monkeypatch):
    """No NSFW rows removed -> no admin email (don't spam the inbox every
    poll cycle when there's nothing to report)."""
    from service.cron.garbagerecords import delete_garbage_records_once
    import service.cron.garbagerecords as garbagerecords_mod

    mock_send = MagicMock(return_value=None)
    monkeypatch.setattr(garbagerecords_mod.aws_smtp, 'send', mock_send)

    asyncio.run(delete_garbage_records_once())

    assert mock_send.call_count == 0


# ---------------------------------------------------------------------------
# F21: beta re-opt-in after unsubscribe
# ---------------------------------------------------------------------------

def test_register_returns_created_for_brand_new_email():
    email = f'beta-new-{uuid4()}@example.org'
    with api_tx() as tx:
        try:
            result = register(tx, email, None)
            assert result == (True, False), \
                'a brand-new signup must report created=True, resubscribed=False'
        finally:
            tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))


def test_register_is_a_noop_for_an_existing_subscribed_email():
    email = f'beta-existing-{uuid4()}@example.org'
    with api_tx() as tx:
        try:
            first = register(tx, email, None)
            assert first == (True, False)

            second = register(tx, email, None)
            assert second == (False, False), \
                'repeat opt-in while still subscribed must be a true no-op ' \
                '(neither created nor resubscribed)'
        finally:
            tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))


def test_beta_reoptin_clears_unsubscribe_and_reports_resubscribed():
    email = f'reopt-{uuid4()}@example.org'
    with api_tx() as tx:
        tx.execute(
            "INSERT INTO beta_signup (email, unsubscribed_at) "
            "VALUES (%(e)s, NOW()) "
            "ON CONFLICT (email) DO UPDATE SET unsubscribed_at = NOW()",
            dict(e=email),
        )

    try:
        with api_tx() as tx:
            result = register(tx, email, None)
        assert result == (False, True), \
            'explicit re-opt-in after unsubscribe must clear it and report ' \
            'resubscribed=True (F21)'

        with api_tx() as tx:
            row = tx.execute(
                "SELECT unsubscribed_at FROM beta_signup WHERE email = %(e)s",
                dict(e=email),
            ).fetchone()
            assert row['unsubscribed_at'] is None, \
                're-opt-in must clear unsubscribed_at, not silently no-op (F21)'
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))


def test_beta_route_reoptin_sends_welcome_exactly_once_per_transition(client, monkeypatch):
    """Route-level: the welcome + admin notice fire on brand-new signup and
    on re-opt-in-after-unsubscribe, but NOT on a repeat click while already
    subscribed (F21)."""
    import service.api.beta_routes as beta_routes_mod

    mock_welcome = MagicMock(return_value=None)
    mock_admin_notice = MagicMock(return_value=None)
    monkeypatch.setattr(beta_routes_mod, 'send_beta_welcome_async', mock_welcome)
    monkeypatch.setattr(beta_routes_mod, 'send_beta_optin_notice_async', mock_admin_notice)

    email = f'route-reopt-{uuid4()}@example.org'

    try:
        # 1) Brand-new signup -> welcome sent, isNew=True.
        r1 = client.post('/beta-tester', json={'email': email})
        assert r1.status_code == 200
        assert r1.get_json()['isNew'] is True
        assert mock_welcome.call_count == 1
        assert mock_admin_notice.call_count == 1

        # 2) Repeat opt-in while still subscribed -> genuine no-op, no re-email.
        r2 = client.post('/beta-tester', json={'email': email})
        assert r2.status_code == 200
        assert r2.get_json()['isNew'] is False
        assert mock_welcome.call_count == 1, \
            'repeat opt-in on an unchanged subscription must not re-email'
        assert mock_admin_notice.call_count == 1

        # 3) Unsubscribe, then re-opt-in -> welcome sent again (F21).
        with api_tx() as tx:
            tx.execute(
                "UPDATE beta_signup SET unsubscribed_at = NOW() WHERE email = %(e)s",
                dict(e=email),
            )

        r3 = client.post('/beta-tester', json={'email': email})
        assert r3.status_code == 200
        assert r3.get_json()['isNew'] is False, \
            'a resubscribe is not a brand-new row (public isNew contract unchanged)'
        assert mock_welcome.call_count == 2, \
            're-opt-in after unsubscribe must send the welcome email again (F21)'
        assert mock_admin_notice.call_count == 2

        with api_tx() as tx:
            row = tx.execute(
                "SELECT unsubscribed_at FROM beta_signup WHERE email = %(e)s",
                dict(e=email),
            ).fetchone()
            assert row['unsubscribed_at'] is None
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))
