"""Lease-bound receipts and delivery state separate from withdrawal (Wave 1
F04). `_make_eligible` and `_photo` copied from tests/test_spotlight_revisions.py
on purpose -- test files in this suite do not import from each other."""
import pytest
from database import api_tx
from service.spotlight.queue import create_candidate, record_receipt, set_status, TRANSITIONS
from service.spotlight.revisions import current_revision, attach_render, record_consent
from service.spotlight.withdrawal import withdraw_member


def _make_eligible(make_person, name='Elig', gender='Woman'):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL
             WHERE id = %(id)s""", dict(id=p['id']))
        # photo has NOT NULL blurhash and hash columns with no default (checked \d photo);
        # uuid is a text column (not native uuid type) but gen_random_uuid() casts in fine.
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (gen_random_uuid(), %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""", dict(id=p['id']))
    return p


def _photo(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s ORDER BY position LIMIT 1", dict(id=pid)).fetchone()['u']


def _claimed(tx, pid):
    rk = create_candidate(tx, kind='welcome', subject_person_id=pid, caption='c', created_by='t')
    rev = current_revision(tx, rk); attach_render(tx, rev['id'], 'h', 'k', 'https://cdn/k.png'); record_consent(tx, rev['id'], pid, 'subject')
    tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall() if r['request_key'] == rk]
    return rk, sorted(rows, key=lambda r: r['platform'])


def _row(tx, qid):
    return tx.execute("SELECT * FROM publishing_queue WHERE id = %(id)s", dict(id=qid)).fetchone()


def test_late_receipt_after_withdrawal_is_recorded_and_removal_filed(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert out['left_attempting'] == 2
        fb = rows[0]
        assert record_receipt(tx, fb['id'], fb['lease_token'], 'published', external_post_id='1_2', post_url='https://www.facebook.com/1_2') == 'recorded'
        r = _row(tx, fb['id'])
        assert (r['status'], r['delivery_state'], r['external_post_id'], r['post_url']) == ('published', 'published', '1_2', 'https://www.facebook.com/1_2')
        assert r['cancellation_requested_at'] is not None
        tasks = tx.execute("SELECT person_id, request_key, external_post_id, done_at FROM spotlight_removal_task WHERE queue_id = %(id)s", dict(id=fb['id'])).fetchall()
        assert [(t['person_id'], t['request_key'], t['external_post_id'], t['done_at']) for t in tasks] == [(p['id'], rk, '1_2', None)]


def test_published_without_withdrawal_files_no_task(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        record_receipt(tx, rows[0]['id'], rows[0]['lease_token'], 'published', external_post_id='9')
        assert tx.execute("SELECT count(*) AS n FROM spotlight_removal_task WHERE queue_id = %(id)s", dict(id=rows[0]['id'])).fetchone()['n'] == 0


def test_wrong_or_missing_lease_rejected(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        with pytest.raises(ValueError, match='lease_mismatch'):
            record_receipt(tx, rows[0]['id'], 'f' * 32, 'published', external_post_id='9')
        with pytest.raises(ValueError, match='lease_required'):
            record_receipt(tx, rows[0]['id'], None, 'published', external_post_id='9')
        assert _row(tx, rows[0]['id'])['status'] == 'processing'


def test_duplicate_receipt_is_noop(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        withdraw_member(tx, p['id'], 'opt_out')
        q = rows[0]
        assert record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='9') == 'recorded'
        assert record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='9') == 'already'
        assert tx.execute("SELECT count(*) AS n FROM spotlight_removal_task WHERE queue_id = %(id)s", dict(id=q['id'])).fetchone()['n'] == 1
        with pytest.raises(ValueError, match='not_processing'):
            record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='10')


def test_published_requires_external_id(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        with pytest.raises(ValueError, match='external_id_required'):
            record_receipt(tx, rows[0]['id'], rows[0]['lease_token'], 'published')


def test_delivery_unknown_parks_and_is_never_reclaimed(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        record_receipt(tx, q['id'], q['lease_token'], 'delivery_unknown', error='no confirmation')
        r = _row(tx, q['id'])
        assert (r['status'], r['delivery_state'], r['lease_until']) == ('review', 'delivery_unknown', None)
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() - interval '1 hour' WHERE id = %(id)s", dict(id=q['id']))
        again = [x for x in tx.execute("SELECT * FROM claim_spotlight_posts(50)").fetchall() if x['id'] == q['id']]
        assert again == []
        assert record_receipt(tx, q['id'], q['lease_token'], 'delivery_unknown', error='no confirmation') == 'already'


def test_failed_keeps_retry_path(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        record_receipt(tx, q['id'], q['lease_token'], 'failed', error='boom')
        r = _row(tx, q['id'])
        assert (r['status'], r['delivery_state']) == ('failed', 'failed')
        again = [x for x in tx.execute("SELECT * FROM claim_spotlight_posts(50)").fetchall() if x['id'] == q['id']]
        assert len(again) == 1 and again[0]['lease_token'] != q['lease_token'] and again[0]['delivery_state'] == 'attempting'


def test_set_status_cannot_leave_processing(make_person):
    p = _make_eligible(make_person)
    assert 'processing' not in TRANSITIONS
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        with pytest.raises(ValueError, match='bad_transition'):
            set_status(tx, rows[0]['id'], 'published', external_post_id='9')


def test_complete_route_requires_lease_and_maps_conflicts(make_person, client, monkeypatch):
    import service.api.admin.spotlight_routes as routes
    sent = []
    monkeypatch.setattr(routes, '_send_card_live', lambda *a: sent.append(a))
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        withdraw_member(tx, p['id'], 'opt_out')
        q = rows[0]; qid = str(q['id']); lease = q['lease_token']
    h = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.post(f'/admin/growth/queue/{qid}/complete', json=dict(status='published', external_post_id='1'), headers=h).status_code == 400
    r = client.post(f'/admin/growth/queue/{qid}/complete', json=dict(status='published', external_post_id='1', lease_token='f' * 32), headers=h)
    assert r.status_code == 409 and r.get_json()['error'] == 'lease_mismatch'
    r = client.post(f'/admin/growth/queue/{qid}/complete', json=dict(status='published', external_post_id='1', lease_token=lease), headers=h)
    assert r.status_code == 200 and r.get_json() == dict(ok=True, status='published', already=False)
    r = client.post(f'/admin/growth/queue/{qid}/complete', json=dict(status='published', external_post_id='1', lease_token=lease), headers=h)
    assert r.status_code == 200 and r.get_json()['already'] is True
    assert sent == []   # withdrawn member never gets the live email


def test_claim_route_returns_lease_token(make_person, client):
    from service.spotlight.queue import set_setting
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
        set_setting(tx, 'scheduler_enabled', 'true')
    try:
        r = client.post('/admin/growth/queue/claim', json=dict(max=50), headers={'X-Growth-Cron': 'test-cron-secret'})
        mine = [x for x in r.get_json() if x['request_key'] == rk]
        assert len(mine) == 2 and all(isinstance(x['lease_token'], str) and len(x['lease_token']) == 32 for x in mine)
    finally:
        with api_tx() as tx:
            set_setting(tx, 'scheduler_enabled', 'false')


def test_late_receipt_on_a_roundup_files_one_unattributed_task():
    """Fix round 1, ruling 1: `record_receipt` does not know WHO withdrew --
    a roundup row has no subject -- so the late removal task it files is
    attributed to no one (`person_id NULL`); the withdrawing member's own
    `withdraw_member` call is what names them, on whichever rows it reached
    directly."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rev = current_revision(tx, rk)
        attach_render(tx, rev['id'], 'h', 'k', 'https://cdn/k.png')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
        rows = sorted([r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall()
                       if r['request_key'] == rk], key=lambda r: r['platform'])
        fb = rows[0]
        # No `withdraw_member` call here on purpose: the brief's ruling is
        # about what `record_receipt` does on its own when a row it is
        # completing already carries the stamp, however it got there.
        tx.execute("UPDATE publishing_queue SET cancellation_requested_at = NOW() WHERE id = %(id)s", dict(id=fb['id']))
        assert record_receipt(tx, fb['id'], fb['lease_token'], 'published', external_post_id='1_2') == 'recorded'
        tasks = tx.execute(
            "SELECT person_id, request_key, external_post_id, done_at FROM spotlight_removal_task WHERE queue_id = %(id)s",
            dict(id=fb['id'])).fetchall()
        assert [(t['person_id'], t['request_key'], t['external_post_id'], t['done_at']) for t in tasks] == [(None, rk, '1_2', None)]


def test_complete_route_rejects_a_non_string_lease_token(make_person, client):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]; qid = str(q['id'])
    h = {'X-Growth-Cron': 'test-cron-secret'}
    r = client.post(f'/admin/growth/queue/{qid}/complete',
                    json=dict(status='published', external_post_id='1', lease_token=12345), headers=h)
    assert r.status_code == 400 and r.get_json()['error'] == 'lease_required'
    with api_tx() as tx:
        assert _row(tx, q['id'])['status'] == 'processing'


def test_duplicate_receipt_is_not_audited(make_person, client, monkeypatch):
    """Fix round 1, ruling 3: a receipt that changes nothing earns no audit
    row -- only a `'recorded'` result does. The cron path's own `_audit`
    just prints (no session to attribute to), so the assertion is on
    `_audit` itself rather than `admin_audit_log`, the same way other tests
    in this suite monkeypatch a route-level function to observe a call."""
    import service.api.admin.spotlight_routes as routes
    calls = []
    real_audit = routes._audit

    def _spy(tx, s, action, **metadata):
        calls.append((action, metadata))
        return real_audit(tx, s, action, **metadata)

    monkeypatch.setattr(routes, '_audit', _spy)
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]; qid = str(q['id']); lease = q['lease_token']
    h = {'X-Growth-Cron': 'test-cron-secret'}
    body = dict(status='published', external_post_id='1', lease_token=lease)
    assert client.post(f'/admin/growth/queue/{qid}/complete', json=body, headers=h).status_code == 200
    assert len(calls) == 1
    r = client.post(f'/admin/growth/queue/{qid}/complete', json=body, headers=h)
    assert r.status_code == 200 and r.get_json()['already'] is True
    assert len(calls) == 1   # the duplicate call never reaches _audit at all
