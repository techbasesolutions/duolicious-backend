"""Task 9 (F12): the three named controls replace the one `scheduler_enabled`
switch. `invites_enabled` gates candidate creation and E4; `publication_enabled`
gates claiming and publishing; `external_access_enabled` is the emergency stop
for every outbound platform call, removals included.

`_make_eligible` is copied from tests/test_spotlight_queue.py on purpose --
test files in this suite do not import from each other.
"""
from __future__ import annotations

import pytest
from database import api_tx
from service.spotlight.queue import create_candidate, set_setting, settings

H = {'X-Growth-Cron': 'test-cron-secret'}

# The seeded defaults (migration 0044): every test that flips one of these
# away from its default restores all four in a finally block, so a failed
# assertion never leaks a changed setting into a later test.
_DEFAULTS = dict(publication_enabled='false', external_access_enabled='true',
                 invites_enabled='true', approvals_enabled='false')


def _set(tx, **kv):
    for k, v in kv.items():
        set_setting(tx, k, v)


def _restore_defaults(tx):
    _set(tx, **_DEFAULTS)


def _make_eligible(make_person, name='Elig', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        # photo has NOT NULL blurhash and hash columns with no default; uuid is a
        # text column but gen_random_uuid() casts in fine (task 2 report).
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (gen_random_uuid(), %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""",
                   dict(id=p['id']))
    return p


def test_legacy_keys_gone_and_rejected():
    with api_tx() as tx:
        assert not {'scheduler_enabled', 'auto_welcome', 'auto_roundup'} & set(settings(tx))
        for k in ('scheduler_enabled', 'auto_welcome', 'auto_roundup'):
            with pytest.raises(ValueError, match='bad_setting'):
                set_setting(tx, k, 'true')


def test_claim_reports_paused_and_halted(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    try:
        with api_tx() as tx: _set(tx, publication_enabled='false', external_access_enabled='true')
        body = client.post('/admin/growth/queue/claim', json=dict(max=50), headers=H).get_json()
        assert body['claimed'] == [] and body['paused'] is True and body['halted'] is False and 'reaped' in body
        with api_tx() as tx: _set(tx, publication_enabled='true', external_access_enabled='false')
        body = client.post('/admin/growth/queue/claim', json=dict(max=50), headers=H).get_json()
        assert body['claimed'] == [] and body['halted'] is True
        with api_tx() as tx: _set(tx, external_access_enabled='true')
        body = client.post('/admin/growth/queue/claim', json=dict(max=50), headers=H).get_json()
        assert any(r['request_key'] == rk for r in body['claimed']) and body['paused'] is False and body['halted'] is False
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def _serve_first(tx, rk):
    """Wave 3d Task 4: the worker's `pending=1` list is served earliest
    deadline first and capped at 200, and this suite commits to one shared
    database. A task this test looks for there is dated ahead of every open
    task earlier tests left behind, or enough leftovers would push it out."""
    tx.execute("""UPDATE spotlight_removal_task
                     SET deadline_at = (SELECT COALESCE(MIN(deadline_at), NOW()) - interval '1 day'
                                          FROM spotlight_removal_task WHERE done_at IS NULL)
                   WHERE request_key = %(rk)s AND done_at IS NULL""", dict(rk=rk))


def test_removals_halted_by_emergency_stop_only(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '5' WHERE request_key = %(rk)s", dict(rk=rk))
        from service.spotlight.withdrawal import withdraw_member; withdraw_member(tx, p['id'], 'opt_out')
        _serve_first(tx, rk)
    try:
        with api_tx() as tx: _set(tx, publication_enabled='false', external_access_enabled='true')
        body = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
        assert body['halted'] is False and any(t['request_key'] == rk for t in body['tasks'])   # paused publication does not pause cleanup
        with api_tx() as tx: _set(tx, external_access_enabled='false')
        body = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
        # Wave 2 Task 5: the task list is withheld under the stop, but the two
        # counts are not -- a stop that has been engaged for a while is
        # exactly when a removal backlog must stay visible.
        assert body['tasks'] == [] and body['halted'] is True
        assert isinstance(body['overdue'], int) and isinstance(body['outstanding_cleanup'], int)
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_invites_gate_candidates_and_creation(client, make_person):
    p = _make_eligible(make_person)
    try:
        with api_tx() as tx: _set(tx, invites_enabled='false')
        body = client.get('/admin/growth/candidates', headers=H).get_json()
        # Wave 3b, task 7: invites_pending_oldest_days rides alongside the
        # count, None here since invites are paused and nothing is computed.
        assert body == dict(welcomes=[], roundup_due=False, invites_enabled=False, invites_pending=0,
                            invites_pending_oldest_days=None)
        r = client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H)
        assert r.status_code == 409 and r.get_json() == dict(error='invites_paused')
        assert client.post('/admin/growth/spotlight/roundup', json={}, headers=H).status_code == 409
        with api_tx() as tx: _set(tx, invites_enabled='true')
        body = client.get('/admin/growth/candidates', headers=H).get_json()
        assert body['invites_enabled'] is True and any(w['person_id'] == p['id'] for w in body['welcomes'])
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_welcome_cohort_is_by_sign_up_time(client, make_person):
    # Depends on invites_enabled being true; set it explicitly rather than
    # trusting the seeded default in case an earlier test left it changed.
    with api_tx() as tx: _set(tx, invites_enabled='true')
    fresh = _make_eligible(make_person, name='Fresh'); old = _make_eligible(make_person, name='Old', gender='Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '2 days', spotlight_opt_in_at = NOW() - interval '20 days' WHERE id = %(p)s", dict(p=fresh['id']))
        tx.execute("UPDATE person SET sign_up_time = NOW() - interval '20 days', spotlight_opt_in_at = NOW() - interval '1 day' WHERE id = %(p)s", dict(p=old['id']))
    ids = {w['person_id'] for w in client.get('/admin/growth/candidates', headers=H).get_json()['welcomes']}
    assert fresh['id'] in ids and old['id'] not in ids


def test_roundup_business_key_converges(client):
    # Depends on invites_enabled being true; set it explicitly (see above).
    with api_tx() as tx: _set(tx, invites_enabled='true')
    from datetime import datetime, timezone
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    key = f"roundup:{y}-W{w:02d}"
    with api_tx() as tx:
        tx.execute("DELETE FROM publishing_queue WHERE request_key = %(k)s", dict(k=key))
    first = client.post('/admin/growth/spotlight/roundup', json={}, headers=H).get_json()
    second = client.post('/admin/growth/spotlight/roundup', json={}, headers=H).get_json()
    assert first['request_key'] == key and second == dict(request_key=key, already=True)
    with api_tx() as tx:
        assert tx.execute("SELECT count(*) AS n FROM publishing_queue WHERE request_key = %(k)s", dict(k=key)).fetchone()['n'] == 2
        tx.execute("DELETE FROM publishing_queue WHERE request_key = %(k)s", dict(k=key))
        with pytest.raises(ValueError, match='bad_request_key'):
            create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t', request_key='Bad Key!')


def test_convergence_checks_kind_not_just_key(make_person):
    """A business key belongs to one kind. A collision under someone else's
    key (an accidental reuse, not the same caller racing itself) is refused
    the same way an invalid pattern is -- it is never treated as the
    converge-and-return-unchanged case."""
    p = _make_eligible(make_person)
    key = 'shared-convergence-key'
    with api_tx() as tx:
        tx.execute("DELETE FROM publishing_queue WHERE request_key = %(k)s", dict(k=key))
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t', request_key=key)
            assert rk == key
            with pytest.raises(ValueError, match='bad_request_key'):
                create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t', request_key=key)
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM publishing_queue WHERE request_key = %(k)s", dict(k=key))
            tx.execute("DELETE FROM spotlight_revision WHERE request_key = %(k)s", dict(k=key))
            tx.execute("DELETE FROM campaign_link WHERE kind = %(k)s", dict(k=f'post:{key}'))


# --- Added from the Task 2 review ---------------------------------------
# A member must never receive an invite they cannot act on, and the 7-day
# clock only runs while they can.

def test_expire_approvals_paused_while_approvals_disabled(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET created_at = NOW() - interval '8 days' WHERE request_key = %(rk)s", dict(rk=rk))
    try:
        with api_tx() as tx: _set(tx, approvals_enabled='false')
        body = client.post('/admin/growth/queue/expire-approvals', json={}, headers=H).get_json()
        assert body == dict(cancelled=0, approvals_paused=True)
        with api_tx() as tx:
            statuses = {r['status'] for r in tx.execute(
                "SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}
            assert statuses == {'awaiting_member'}   # the clock did not run
        with api_tx() as tx: _set(tx, approvals_enabled='true')
        body = client.post('/admin/growth/queue/expire-approvals', json={}, headers=H).get_json()
        assert body['cancelled'] >= 2 and 'approvals_paused' not in body
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_welcome_creates_candidate_without_invite_when_approvals_disabled(client, make_person, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    sent = []
    audited = []
    monkeypatch.setattr(sr, '_enqueue_card_ready', lambda tx, pid, rk: sent.append((pid, rk)))
    monkeypatch.setattr(sr, '_audit', lambda tx, s, action, **metadata: audited.append((action, metadata)))
    p = _make_eligible(make_person)
    try:
        with api_tx() as tx: _set(tx, invites_enabled='true', approvals_enabled='false')
        r = client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H)
        assert r.status_code == 200
        rk = r.get_json()['request_key']
        assert sent == []   # E4 withheld: the member could not act on it yet
        with api_tx() as tx:
            statuses = {row['status'] for row in tx.execute(
                "SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}
        assert statuses == {'awaiting_member'}   # the candidate still exists
        assert audited == [('growth.queue.welcome', dict(person_id=p['id'], request_key=rk, invite_sent=False))]
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_invites_withheld_during_pause_are_sent_when_approvals_open(client, make_person):
    a = _make_eligible(make_person, name='A'); b = _make_eligible(make_person, name='B', gender='Man')
    with api_tx() as tx: _set(tx, approvals_enabled='false', invites_enabled='true')
    try:
        for p in (a, b):
            assert client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H).status_code == 200
        with api_tx('read committed') as tx:
            assert tx.execute("SELECT count(*) AS n FROM email_outbox WHERE campaign = 'e4' AND person_id = ANY(%(ids)s)", dict(ids=[a['id'], b['id']])).fetchone()['n'] == 0
        assert client.get('/admin/growth/candidates', headers=H).get_json()['invites_pending'] >= 2
        r = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H)
        assert r.status_code == 409 and r.get_json() == dict(error='approvals_paused')
        with api_tx() as tx: _set(tx, approvals_enabled='true')
        first = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        second = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        assert first['queued'] >= 2 and second['queued'] == 0
        with api_tx('read committed') as tx:
            assert tx.execute("SELECT count(*) AS n FROM email_outbox WHERE campaign = 'e4' AND person_id = ANY(%(ids)s) AND state = 'queued'", dict(ids=[a['id'], b['id']])).fetchone()['n'] == 2
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_invite_pending_cancels_a_request_whose_subject_became_ineligible(client, make_person):
    """Fix round 1 ruling 1: a withheld invite that can never become sendable
    reaches a terminal state instead of sitting in invites_pending forever --
    an ineligible subject's request is cancelled outright, drops out of the
    backlog, and a second call finds nothing left to do for it.

    Fix wave item 5 narrowed which failures count as "can never become
    sendable" to the three that really are terminal, so this now uses
    `pending_deletion` (the member asked for their account to go) rather than
    `under_18`, which simply means "not yet"."""
    p = _make_eligible(make_person)
    with api_tx() as tx: _set(tx, approvals_enabled='false', invites_enabled='true')
    try:
        assert client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H).status_code == 200
        with api_tx() as tx:
            tx.execute("UPDATE person SET deletion_requested_at = NOW() WHERE id = %(id)s", dict(id=p['id']))
        with api_tx() as tx: _set(tx, approvals_enabled='true')
        r = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        assert r['cancelled'] >= 1 and r['queued'] == 0
        with api_tx('read committed') as tx:
            statuses = {row['status'] for row in tx.execute(
                "SELECT status FROM publishing_queue WHERE subject_person_id = %(p)s", dict(p=p['id'])).fetchall()}
            errors = {row['error'] for row in tx.execute(
                "SELECT error FROM publishing_queue WHERE subject_person_id = %(p)s", dict(p=p['id'])).fetchall()}
        assert statuses == {'cancelled'} and errors == {'invite_skipped:pending_deletion'}
        second = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        assert second == dict(queued=0, skipped=0, cancelled=0)
    finally:
        with api_tx() as tx:
            tx.execute("UPDATE person SET deletion_requested_at = NULL WHERE id = %(id)s", dict(id=p['id']))
            _restore_defaults(tx)


def test_invite_pending_skips_a_recoverable_failure_and_queues_it_later(client, make_person):
    """Fix wave item 5. Cancelling on ANY eligibility failure threw away
    invites over conditions that clear on their own: a report that is
    dismissed, a verification that lands, a birthday, the 30-day featured
    cooldown running out. Cancellation is not reversible from this route, so
    the member simply never got their card. Only `not_activated`,
    `not_opted_in` and `pending_deletion` are treated as terminal now;
    everything else counts `skipped` and is re-checked on the next run."""
    reporter = _make_eligible(make_person, name='ReportSkipReporter', gender='Man')
    p = _make_eligible(make_person, name='ReportSkipSubject')
    with api_tx() as tx: _set(tx, approvals_enabled='false', invites_enabled='true')
    try:
        assert client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H).status_code == 200
        with api_tx() as tx:
            tx.execute(
                """INSERT INTO skipped (subject_person_id, object_person_id, reported, report_reason)
                   VALUES (%(a)s, %(b)s, TRUE, 'spam')""", dict(a=reporter['id'], b=p['id']))
            _set(tx, approvals_enabled='true')
        first = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        assert first['skipped'] >= 1 and first['cancelled'] == 0 and first['queued'] == 0
        with api_tx('read committed') as tx:
            # The request is untouched and still in the backlog, so the next
            # run reconsiders it rather than having thrown it away.
            assert {row['status'] for row in tx.execute(
                "SELECT status FROM publishing_queue WHERE subject_person_id = %(p)s",
                dict(p=p['id'])).fetchall()} == {'awaiting_member'}
        with api_tx() as tx:
            tx.execute("UPDATE skipped SET reported = FALSE WHERE object_person_id = %(b)s", dict(b=p['id']))
        second = client.post('/admin/growth/spotlight/invite-pending', json={}, headers=H).get_json()
        assert second['queued'] >= 1
        with api_tx('read committed') as tx:
            assert tx.execute(
                """SELECT count(*) AS n FROM email_outbox
                    WHERE campaign = 'e4' AND person_id = %(p)s AND state = 'queued'""",
                dict(p=p['id'])).fetchone()['n'] == 1
    finally:
        with api_tx() as tx: _restore_defaults(tx)


def test_a_cancelled_welcome_does_not_block_a_new_one(client, make_person):
    """Fix wave item 5, second half. The welcome route's duplicate guard
    matched on kind alone, so once invite-pending cancelled a request the
    member could never be offered a welcome card again: the guard saw the
    cancelled row and answered 409 forever. A cancelled row is a request that
    did not happen, so it no longer counts as a duplicate. Rows in every other
    status still do -- the guard exists to stop two live welcome cards for the
    same member."""
    p = _make_eligible(make_person, name='CancelledWelcome')
    with api_tx() as tx: _set(tx, approvals_enabled='true', invites_enabled='true')
    try:
        first = client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H)
        assert first.status_code == 200
        first_rk = first.get_json()['request_key']
        # A live welcome still blocks a second one.
        assert client.post('/admin/growth/spotlight/welcome',
                           json=dict(person_id=p['id']), headers=H).status_code == 409
        with api_tx() as tx:
            tx.execute("UPDATE publishing_queue SET status = 'cancelled', error = 'invite_skipped:not_opted_in' "
                       "WHERE request_key = %(rk)s", dict(rk=first_rk))
        second = client.post('/admin/growth/spotlight/welcome', json=dict(person_id=p['id']), headers=H)
        assert second.status_code == 200
        assert second.get_json()['request_key'] != first_rk
    finally:
        with api_tx() as tx: _restore_defaults(tx)
