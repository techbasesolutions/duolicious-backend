"""Lease-bound receipts and delivery state separate from withdrawal (Wave 1
F04). `_make_eligible` and `_photo` copied from tests/test_spotlight_revisions.py
on purpose -- test files in this suite do not import from each other."""
import secrets
import psycopg
import pytest
import database
from database import api_tx
from service.spotlight.queue import create_candidate, lock_request_rows, record_receipt, set_status, TRANSITIONS
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
        # uuid is a text column, and a real photo id is a 64-character hex string
        # (upload_photo mints them with secrets.token_hex(32)), so the fixture does too.
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (%(u)s, %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""", dict(u=secrets.token_hex(32), id=p['id']))
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
    monkeypatch.setattr(routes, '_enqueue_card_live', lambda tx, *a: sent.append(a))
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
        set_setting(tx, 'publication_enabled', 'true')
    try:
        r = client.post('/admin/growth/queue/claim', json=dict(max=50), headers={'X-Growth-Cron': 'test-cron-secret'})
        mine = [x for x in r.get_json()['claimed'] if x['request_key'] == rk]
        assert len(mine) == 2 and all(isinstance(x['lease_token'], str) and len(x['lease_token']) == 32 for x in mine)
    finally:
        with api_tx() as tx:
            set_setting(tx, 'publication_enabled', 'false')


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


def _reap(tx, qid):
    """Expire this row's lease and run the reaper, exactly as the claim route
    does on every poll: the row lands in `review` with error `lease_expired`,
    keeping the lease_token that produced it."""
    from service.spotlight.queue import reap_expired_leases
    tx.execute("UPDATE publishing_queue SET lease_until = NOW() - interval '1 minute' WHERE id = %(id)s", dict(id=qid))
    reap_expired_leases(tx)


def test_reaped_row_records_a_late_published_receipt(make_person):
    """Fix wave item 1: a reaped row may have a LIVE post behind it. The
    lease holder is the only caller that can say so, so its late `published`
    receipt under the same lease is recorded rather than refused."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        _reap(tx, q['id'])
        assert _row(tx, q['id'])['status'] == 'review'
        assert record_receipt(tx, q['id'], q['lease_token'], 'published',
                              external_post_id='1_7', post_url='https://www.facebook.com/1_7') == 'recorded'
        r = _row(tx, q['id'])
        assert (r['status'], r['delivery_state'], r['external_post_id'], r['post_url'], r['error']) == (
            'published', 'published', '1_7', 'https://www.facebook.com/1_7', None)


def test_reaped_row_after_withdrawal_files_the_removal_task(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        _reap(tx, q['id'])
        withdraw_member(tx, p['id'], 'opt_out')
        assert record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='1_8') == 'recorded'
        tasks = tx.execute(
            "SELECT reason, external_post_id FROM spotlight_removal_task WHERE queue_id = %(id)s",
            dict(id=q['id'])).fetchall()
        assert [(t['reason'], t['external_post_id']) for t in tasks] == [('delete_via_api', '1_8')]


def test_delivery_unknown_row_records_a_definite_receipt(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        record_receipt(tx, q['id'], q['lease_token'], 'delivery_unknown', error='no confirmation')
        assert record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='1_9') == 'recorded'
        r = _row(tx, q['id'])
        assert (r['status'], r['delivery_state'], r['external_post_id']) == ('published', 'published', '1_9')


def test_reaped_row_refuses_a_wrong_lease(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        _reap(tx, q['id'])
        with pytest.raises(ValueError, match='lease_mismatch'):
            record_receipt(tx, q['id'], 'f' * 32, 'published', external_post_id='1_10')
        assert _row(tx, q['id'])['status'] == 'review'


def test_operator_parked_review_row_still_refuses_a_published_receipt(make_person):
    """Only a row whose delivery is genuinely unresolved is recoverable. An
    operator's own `review` decision (delivery_state reset to 'none') is not."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        record_receipt(tx, q['id'], q['lease_token'], 'review', error='ineligible now')
        with pytest.raises(ValueError, match='not_processing'):
            record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='1_11')


def test_lock_request_rows_holds_both_platform_rows_of_the_request(make_person):
    """Residual fix item 1: the complete and reconcile routes now take
    `lock_request_rows`'s wide lock, over EVERY row of the request key, before
    reading anything off their own row -- replacing the single-row
    `lock_queue_row` they used to call. Two sibling platform rows completing
    at once used to each hold only their own row's lock, then both reach
    `is_first_confirmation` (occurrence.py), whose own `FOR UPDATE` spans the
    whole key -- a real deadlock. Proven directly here with a second, raw
    connection (the shared `api_tx()` connection is a single global lock, so
    a genuine second transaction needs its own connection, the same way
    tests/test_review_reliability.py's concurrency tests do): while the first
    connection's `lock_request_rows` transaction is still open, a `FOR UPDATE
    NOWAIT` from a second connection against the SIBLING row is refused
    outright, proving the first transaction holds that row's lock too, not
    just its own."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
    fb, ig = rows
    conn = psycopg.connect(database._api_conninfo, row_factory=psycopg.rows.dict_row)
    try:
        locked = lock_request_rows(conn, fb['id'])
        # Both rows of the request, ordered by id -- the same order every
        # caller (this one, and is_first_confirmation's later re-lock) uses,
        # which is what rules out the two-sibling deadlock: whichever
        # transaction gets here first always acquires the whole set before
        # the other can acquire any of it.
        assert [r['id'] for r in locked] == sorted([fb['id'], ig['id']], key=str)
        with psycopg.connect(database._api_conninfo, row_factory=psycopg.rows.dict_row) as other:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute("SELECT id FROM publishing_queue WHERE id = %(id)s FOR UPDATE NOWAIT",
                              dict(id=ig['id']))
    finally:
        conn.rollback()
        conn.close()


def test_delivery_unknown_after_processing_withdrawal_files_and_upgrades_investigate_task(make_person):
    """Residual fix item 2(a): a member who withdraws WHILE their row is still
    `processing` gets only the cancellation stamp then -- `withdraw_member`
    step 3 files no task for a still-processing row, since its lease holder is
    still expected to report back. When that lease holder's own receipt comes
    back `delivery_unknown`, the row has just reached the exact unresolved
    state withdrawal itself hands an `investigate` task for when it is
    already in that state at withdrawal time, so `record_receipt` files one
    now. A later `published` receipt under the same lease upgrades that same
    task in place (fix wave item 1's existing upgrade path) rather than
    filing a second one beside it -- exactly one task throughout."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert out['left_attempting'] == 2 and out['removal_tasks'] == 0
        assert _row(tx, q['id'])['status'] == 'processing'
        assert record_receipt(tx, q['id'], q['lease_token'], 'delivery_unknown',
                              error='no confirmation') == 'recorded'
        tasks = tx.execute(
            "SELECT reason, external_post_id FROM spotlight_removal_task WHERE queue_id = %(id)s",
            dict(id=q['id'])).fetchall()
        assert [(t['reason'], t['external_post_id']) for t in tasks] == [('investigate', None)]
        assert record_receipt(tx, q['id'], q['lease_token'], 'published', external_post_id='1_12') == 'recorded'
        tasks = tx.execute(
            "SELECT reason, external_post_id FROM spotlight_removal_task WHERE queue_id = %(id)s",
            dict(id=q['id'])).fetchall()
        assert [(t['reason'], t['external_post_id']) for t in tasks] == [('delete_via_api', '1_12')]


def test_reap_after_processing_withdrawal_files_investigate_task(make_person):
    """Residual fix item 2(b): the same transition into an unresolved state,
    reached via `reap_expired_leases` instead of a receipt -- a withdrawn
    member's row that ages out of its lease lands in `review` with nobody
    left to ever report back, so the reaper files the `investigate` task
    itself, the moment it happens, rather than waiting on an event that may
    never come."""
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk, rows = _claimed(tx, p['id'])
        q = rows[0]
        out = withdraw_member(tx, p['id'], 'opt_out')
        assert out['removal_tasks'] == 0
        assert _row(tx, q['id'])['status'] == 'processing'
        _reap(tx, q['id'])
        assert _row(tx, q['id'])['status'] == 'review'
        tasks = tx.execute(
            "SELECT reason, external_post_id FROM spotlight_removal_task WHERE queue_id = %(id)s",
            dict(id=q['id'])).fetchall()
        assert [(t['reason'], t['external_post_id']) for t in tasks] == [('investigate', None)]


def test_e5_enqueue_failure_never_rolls_back_the_receipt(make_person, client, monkeypatch):
    """Fix round 1, ruling 2. By the time E5 runs the post is already live on
    the platform and the receipt proving it is in this transaction. Losing
    that receipt because the email could not be built would orphan a real
    post that nothing would then reconcile or remove, so the failure is
    contained in a savepoint, audited, and the receipt commits regardless.

    The savepoint matters as much as the catch: `enqueue_card_live` mints the
    /s/ campaign link BEFORE it builds the html, so without the rollback a
    failed message would leave a stray link row behind."""
    import emails.spotlight_card_live as card_live_mod

    def _boom(*a, **kw):
        raise RuntimeError('template exploded')

    monkeypatch.setattr(card_live_mod, 'card_live_html', _boom)
    p = _make_eligible(make_person, name='E5Boom')
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s",
                   dict(e=f'e5-boom-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk, rows = _claimed(tx, p['id'])
        tx.execute("UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s",
                   dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
        fb = rows[0]
        qid, lease = str(fb['id']), fb['lease_token']

    r = client.post(f'/admin/growth/queue/{qid}/complete',
                    json=dict(status='published', external_post_id='1_9', lease_token=lease,
                              post_url='https://www.facebook.com/1_9'),
                    headers={'X-Growth-Cron': 'test-cron-secret'})
    assert r.status_code == 200 and r.get_json() == dict(ok=True, status='published', already=False)

    with api_tx('read committed') as tx:
        queue_row = tx.execute(
            "SELECT status, delivery_state, external_post_id FROM publishing_queue WHERE id = %(id)s",
            dict(id=qid)).fetchone()
        outbox_rows = tx.execute(
            "SELECT count(*) AS n FROM email_outbox WHERE person_id = %(p)s AND campaign = 'e5'",
            dict(p=p['id'])).fetchone()
        # Only E5's link targets the Facebook sharer dialog; create_candidate
        # mints its own `post:<rk>` link at the web base URL for the caption,
        # which is unrelated and must survive.
        links = tx.execute(
            """SELECT count(*) AS n FROM campaign_link
                WHERE kind = %(k)s AND strpos(target_url, 'sharer.php') > 0""",
            dict(k=f'post:{rk}')).fetchone()
        occurrences = tx.execute(
            "SELECT count(*) AS n FROM spotlight_occurrence WHERE request_key = %(rk)s",
            dict(rk=rk)).fetchone()
    # The receipt landed in full: status, delivery state, external id, occurrence.
    assert (queue_row['status'], queue_row['delivery_state'], queue_row['external_post_id']) == (
        'published', 'published', '1_9')
    assert occurrences['n'] == 1
    # And nothing of the failed email survived: no outbox row, and the /s/
    # link the half-built message had already minted was rolled back.
    assert outbox_rows['n'] == 0
    assert links['n'] == 0
