"""The /admin/growth/* spotlight queue endpoints, through the real Flask client.

Covers the two auth paths these routes carry: an admin bearer session, and the
cron shared secret in `X-Growth-Cron`. The session helpers are copied from
tests/test_growth_routes.py and `_make_eligible` from tests/test_spotlight_queue.py
on purpose -- test files in this suite do not import from each other.
"""
from __future__ import annotations

import hashlib
import secrets

import pytest

from database import api_tx
from service.spotlight import set_spotlight_opt_in
from service.spotlight.queue import create_candidate, set_status, set_setting
from service.spotlight.revisions import current_revision, attach_render, create_revision, record_consent, consent_complete


def _session_for(p, signed_in: bool = True) -> str:
    """Mint a real duo_session row and return its bearer token.

    `signed_in=False` is the shape POST /request-otp leaves behind before the
    code is entered: a real session row for a real email that has NOT
    authenticated yet."""
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s",
                           dict(i=p['id'])).fetchone()['email']
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%(h)s, %(e)s, %(p)s, %(s)s, '123456')
            """,
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=p['id'], s=signed_in),
        )
    return tok


def _make_admin(make_person):
    p = make_person(name='SpotlightAdmin')
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s",
                   dict(i=p['id']))
    return p


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


def _render(tx, rk, key='k', url='https://cdn/k.png'):
    """Stand-in for the /admin/growth/queue/<rk>/image route (Task 2): renders
    the request's current revision and stamps every row's own image columns
    and status, the same end state attach_image (pre-Wave-1) used to leave
    behind for a roundup."""
    rev = current_revision(tx, rk)
    attach_render(tx, rev['id'], f'hash-{rk}', key, url)
    tx.execute("UPDATE publishing_queue SET image_key = %(k)s, image_url = %(u)s, status = 'review', updated_at = NOW() WHERE request_key = %(rk)s",
               dict(k=key, u=url, rk=rk))


def test_non_admin_and_bad_secret_are_403(client, make_person):
    p = make_person(name='Nobody')
    tok = _session_for(p)
    assert client.get('/admin/growth/queue', headers={'Authorization': f'Bearer {tok}'}).status_code == 403
    assert client.get('/admin/growth/queue', headers={'X-Growth-Cron': 'wrong'}).status_code == 403


def test_pre_otp_admin_session_is_rejected(client, make_person):
    """A duo_session row exists the moment /request-otp is called, before the
    code is entered. `require_auth` refuses it (expected_sign_in_status=True),
    so the hand-rolled `_session()` on the admin-or-cron routes must too --
    otherwise anyone who knows an admin's email gets admin."""
    admin = _make_admin(make_person)
    tok = _session_for(admin, signed_in=False)
    r = client.get('/admin/growth/queue', headers={'Authorization': f'Bearer {tok}'})
    assert r.status_code == 403
    # The same person, properly signed in, is allowed through.
    good = _session_for(admin)
    assert client.get('/admin/growth/queue', headers={'Authorization': f'Bearer {good}'}).status_code == 200


def test_cron_can_list_claim_and_complete(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        _render(tx, rk)
        set_setting(tx, 'scheduler_enabled', 'true')
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall():
            set_status(tx, r['id'], 'scheduled')
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    H = {'X-Growth-Cron': 'test-cron-secret'}
    claimed = client.post('/admin/growth/queue/claim', json={'max': 5}, headers=H).get_json()
    mine = [r for r in claimed if r['request_key'] == rk]
    assert len(mine) == 2 and all(r['status'] == 'processing' for r in mine)
    r = client.post(f"/admin/growth/queue/{mine[0]['id']}/complete",
                    json={'status': 'published', 'external_post_id': '123', 'lease_token': mine[0]['lease_token']},
                    headers=H)
    assert r.status_code == 200
    rows = client.get('/admin/growth/queue', headers=H).get_json()
    assert any(x['id'] == mine[0]['id'] and x['status'] == 'published' and x['external_post_id'] == '123' for x in rows)
    with api_tx() as tx:
        set_setting(tx, 'scheduler_enabled', 'false')


def test_claim_returns_nothing_when_scheduler_disabled(client):
    assert client.post('/admin/growth/queue/claim', json={'max': 5}, headers={'X-Growth-Cron': 'test-cron-secret'}).get_json() == []


def test_claim_reaps_expired_leases(client, make_person):
    """A `processing` row whose lease expired 20 minutes ago moves to
    `review` with error `lease_expired` and is never returned as claimed --
    an interrupted publish may actually have succeeded, so it must not be
    auto-retried (spec 5)."""
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
        tx.execute("""UPDATE publishing_queue SET status = 'processing',
                             lease_until = NOW() - interval '20 minutes'
                       WHERE id = %(id)s""", dict(id=qid))
    H = {'X-Growth-Cron': 'test-cron-secret'}
    r = client.post('/admin/growth/queue/claim', json={'max': 5, 'shape': 'v2'}, headers=H)
    body = r.get_json()
    assert isinstance(body, dict) and 'claimed' in body and 'reaped' in body
    assert body['reaped'] >= 1
    assert not any(row['id'] == str(qid) for row in body['claimed'])
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT status, error, lease_until FROM publishing_queue WHERE id = %(id)s", dict(id=qid)).fetchone()
    assert row['status'] == 'review' and row['error'] == 'lease_expired' and row['lease_until'] is None
    # The default (no shape key) shape is still a bare array, unchanged for
    # the existing admin worker contract.
    plain = client.post('/admin/growth/queue/claim', json={'max': 5}, headers=H).get_json()
    assert isinstance(plain, list)


def test_queue_due_filter(client, make_person):
    with api_tx() as tx:
        rk_due = create_candidate(tx, kind='roundup', subject_person_id=None, caption='due', created_by='t')
        _render(tx, rk_due, 'k', 'https://cdn/k.png')
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk_due)).fetchall():
            set_status(tx, r['id'], 'scheduled')
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk_due))
        rk_future = create_candidate(tx, kind='roundup', subject_person_id=None, caption='future', created_by='t')
        _render(tx, rk_future, 'k2', 'https://cdn/k2.png')
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk_future)).fetchall():
            set_status(tx, r['id'], 'scheduled')
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() + interval '1 day' WHERE request_key = %(rk)s", dict(rk=rk_future))
    H = {'X-Growth-Cron': 'test-cron-secret'}
    rows = client.get('/admin/growth/queue?status=scheduled&due=1', headers=H).get_json()
    keys = {r['request_key'] for r in rows}
    assert rk_due in keys and rk_future not in keys


def test_image_upload_attaches_and_moves_to_review(client, monkeypatch, make_person):
    import base64
    calls = []
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_put_png', lambda key, data: calls.append((key, len(data))))
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    H = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png}, headers=H).status_code == 200
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'instagram', 'png_base64': png}, headers=H).status_code == 200
    assert [c[0] for c in calls] == [f'spotlight/{rk}-facebook.png', f'spotlight/{rk}-instagram.png']
    with api_tx('read committed') as tx:
        rows = tx.execute("SELECT platform, status, image_url FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
    assert {r['status'] for r in rows} == {'review'}
    # attach_render stamps one url on the revision (Task 2); each row must
    # still end up pointing at its OWN rendered file.
    for r in rows:
        assert r['image_url'].endswith(f"/spotlight/{rk}-{r['platform']}.png")
    bad = base64.b64encode(b'notpng').decode()
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': bad}, headers=H).status_code == 400


def test_image_upload_refuses_a_published_row(client, monkeypatch):
    import base64
    calls = []
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_put_png', lambda key, data: calls.append(key))
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue SET status = 'published', image_key = 'original.png'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk))
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    r = client.post(f'/admin/growth/queue/{rk}/image',
                    json={'platform': 'facebook', 'png_base64': png},
                    headers={'X-Growth-Cron': 'test-cron-secret'})
    assert r.status_code == 409 and r.get_json() == {'error': 'bad_status'}
    assert calls == []
    with api_tx('read committed') as tx:
        row = tx.execute("""SELECT image_key FROM publishing_queue
                             WHERE request_key = %(rk)s AND platform = 'facebook'""",
                         dict(rk=rk)).fetchone()
    assert row['image_key'] == 'original.png'


def test_image_upload_refuses_a_second_render_of_the_same_revision(client, monkeypatch):
    """Fix round 1: once a revision is rendered, a second upload for it is
    refused outright -- no upload attempted, no row or revision column
    touched -- rather than silently re-rendering (which used to be a no-op
    swallowed by a blanket except ValueError: pass)."""
    import base64
    calls = []
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_put_png', lambda key, data: calls.append(key))
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    H = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png}, headers=H).status_code == 200
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'instagram', 'png_base64': png}, headers=H).status_code == 200
    with api_tx('read committed') as tx:
        rev_before = current_revision(tx, rk)
        rows_before = tx.execute(
            "SELECT platform, image_key, image_url FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform",
            dict(rk=rk)).fetchall()
    assert rev_before['asset_hash'] is not None
    calls.clear()
    r = client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png}, headers=H)
    assert r.status_code == 409 and r.get_json() == {'error': 'already_rendered'}
    assert calls == []  # no upload was even attempted
    with api_tx('read committed') as tx:
        rev_after = current_revision(tx, rk)
        rows_after = tx.execute(
            "SELECT platform, image_key, image_url FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform",
            dict(rk=rk)).fetchall()
    assert rev_after['asset_hash'] == rev_before['asset_hash']
    assert rev_after['image_key'] == rev_before['image_key'] and rev_after['image_url'] == rev_before['image_url']
    assert rows_after == rows_before


def test_image_upload_pins_render_to_facebook_regardless_of_upload_order(client, monkeypatch):
    """Fix round 1: the revision's image_key/image_url/asset_hash always
    reflect the facebook row's own upload when one exists, even when
    instagram is uploaded first and completes the set."""
    import base64
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_put_png', lambda key, data: None)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    H = {'X-Growth-Cron': 'test-cron-secret'}
    # instagram first, facebook second (completes the set).
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'instagram', 'png_base64': png}, headers=H).status_code == 200
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png}, headers=H).status_code == 200
    with api_tx('read committed') as tx:
        rev = current_revision(tx, rk)
    assert rev['image_key'] == f'spotlight/{rk}-facebook.png'
    assert rev['image_url'].endswith(f'/spotlight/{rk}-facebook.png')


def test_image_upload_refuses_when_no_revision_is_assigned(client, monkeypatch):
    """Fix round 1: a row whose current_revision_id is NULL is refused before
    any upload -- this should not happen for a request created through
    create_candidate, but the route must fail closed rather than upload
    against nothing."""
    import base64
    calls = []
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_put_png', lambda key, data: calls.append(key))
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET current_revision_id = NULL WHERE request_key = %(rk)s", dict(rk=rk))
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    r = client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': png},
                    headers={'X-Growth-Cron': 'test-cron-secret'})
    assert r.status_code == 409 and r.get_json() == {'error': 'no_revision'}
    assert calls == []


def test_admin_approve_default_slot_and_purge(client, make_person):
    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        _render(tx, rk)
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
    r = client.post(f'/admin/growth/queue/{qid}/approve', json={'scheduled_for': None}, headers=A)
    assert r.status_code == 200 and r.get_json()['status'] == 'scheduled' and r.get_json()['scheduled_for'] is not None
    # Purge is global by design, and the test database persists between runs,
    # so the assertion is scoped to the rows this test created rather than to
    # the table being left empty.
    r = client.post('/admin/growth/queue/purge', json={}, headers=A)
    assert r.status_code == 200
    with api_tx('read committed') as tx:
        mine = tx.execute("SELECT status, error FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
        n = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action IN ('growth.queue.approve','growth.queue.purge')").fetchone()['n']
    assert len(mine) == 2
    assert {row['status'] for row in mine} == {'cancelled'}
    assert {row['error'] for row in mine} == {'purged'}
    assert r.get_json()['cancelled'] >= len(mine)
    assert n >= 2


def test_admin_with_cron_header_still_audits(client, make_person):
    """The cron header must never launder a human mutation past the audit log:
    `_audit` keys on the session, not on the header."""
    admin = _make_admin(make_person); tok = _session_for(admin)
    headers = {'Authorization': f'Bearer {tok}', 'X-Growth-Cron': 'test-cron-secret'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
    with api_tx('read committed') as tx:
        before = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action = 'growth.queue.purge'").fetchone()['n']
    assert client.post('/admin/growth/queue/purge', json={}, headers=headers).status_code == 200
    with api_tx('read committed') as tx:
        row = tx.execute(
            """SELECT actor_email FROM admin_audit_log
                WHERE action = 'growth.queue.purge' ORDER BY created_at DESC LIMIT 1""").fetchone()
        after = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action = 'growth.queue.purge'").fetchone()['n']
        # Scoped to this test's own rows: purge is global, but the assertion
        # must not depend on what else is in a persistent test database.
        mine = tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s",
                          dict(rk=rk)).fetchall()
    assert {r['status'] for r in mine} == {'cancelled'}
    assert after == before + 1
    assert row['actor_email']


def test_removals_listed_and_marked_done(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue
                         SET status = 'published', external_post_id = 'ig-1'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk))
        # Opting out cancels the queue and files a removal task for anything
        # already published (service/spotlight/queue.py::cancel_for_member).
        set_spotlight_opt_in(tx, p['id'], False)
    H = {'X-Growth-Cron': 'test-cron-secret'}
    rows = client.get('/admin/growth/removals?pending=1', headers=H).get_json()
    mine = [r for r in rows if r['request_key'] == rk]
    assert len(mine) == 1
    task = mine[0]
    assert task['platform'] == 'instagram' and task['external_post_id'] == 'ig-1'
    assert task['reason'] == 'manual_instagram' and task['done_at'] is None
    assert client.post(f"/admin/growth/removals/{task['id']}/done", json={}, headers=H).status_code == 200
    with api_tx('read committed') as tx:
        done_at = tx.execute("SELECT done_at FROM spotlight_removal_task WHERE id = %(i)s",
                             dict(i=task['id'])).fetchone()['done_at']
    assert done_at is not None
    assert not any(r['id'] == task['id']
                   for r in client.get('/admin/growth/removals?pending=1', headers=H).get_json())


def test_removal_done_deletes_stored_image(client, make_person, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    deleted = []
    monkeypatch.setattr(sr, 'delete_images', lambda keys: deleted.extend(keys) or len(keys))
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue
                         SET status = 'published', external_post_id = 'ig-2',
                             image_key = 'spotlight/removed.png', image_url = 'https://cdn/removed.png'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk))
        set_spotlight_opt_in(tx, p['id'], False)
    H = {'X-Growth-Cron': 'test-cron-secret'}
    task = [r for r in client.get('/admin/growth/removals?pending=1', headers=H).get_json() if r['request_key'] == rk][0]
    assert client.post(f"/admin/growth/removals/{task['id']}/done", json={}, headers=H).status_code == 200
    assert deleted == ['spotlight/removed.png']
    with api_tx('read committed') as tx:
        row = tx.execute("""SELECT image_key, image_url FROM publishing_queue
                             WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk)).fetchone()
    assert row['image_key'] is None and row['image_url'] is None


def test_candidates_and_welcome(client, make_person, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_send_card_ready', lambda pid, rk: None)
    p = _make_eligible(make_person)
    H = {'X-Growth-Cron': 'test-cron-secret'}
    c = client.get('/admin/growth/candidates', headers=H).get_json()
    assert any(w['person_id'] == p['id'] for w in c['welcomes'])
    r = client.post('/admin/growth/spotlight/welcome', json={'person_id': p['id']}, headers=H)
    assert r.status_code == 200 and r.get_json()['request_key']
    assert client.post('/admin/growth/spotlight/welcome', json={'person_id': p['id']}, headers=H).status_code == 409
    c = client.get('/admin/growth/candidates', headers=H).get_json()
    assert not any(w['person_id'] == p['id'] for w in c['welcomes'])


def test_settings_and_token_health(client, make_person):
    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}; H = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.post('/admin/growth/settings', json={'key': 'auto_welcome', 'value': 'true'}, headers=A).status_code == 200
    assert client.get('/admin/growth/settings', headers=H).get_json()['auto_welcome'] == 'true'
    assert client.post('/admin/growth/settings', json={'key': 'nope', 'value': 'true'}, headers=A).status_code == 400
    assert client.post('/admin/growth/token-health', json={'expires_at': '2027-01-01T00:00:00Z', 'valid': True}, headers=H).status_code == 200
    th = client.get('/admin/growth/token-health', headers=A).get_json()
    assert th['valid'] is True and th['days_left'] > 0
    # Rejections: a non-string / unparsable expiry, and a non-boolean `valid`.
    assert client.post('/admin/growth/token-health', json={'expires_at': 12345, 'valid': True}, headers=H).status_code == 400
    assert client.post('/admin/growth/token-health', json={'expires_at': 'not-a-date', 'valid': True}, headers=H).status_code == 400
    assert client.post('/admin/growth/token-health', json={'expires_at': None, 'valid': 'yes'}, headers=H).status_code == 400
    # A rejected write leaves the stored value alone.
    th = client.get('/admin/growth/token-health', headers=A).get_json()
    assert th['valid'] is True and th['days_left'] > 0 and th['expires_at']
    client.post('/admin/growth/settings', json={'key': 'auto_welcome', 'value': 'false'}, headers=A)


def test_suggest_orders_never_featured_first(client, make_person):
    admin = _make_admin(make_person); tok = _session_for(admin)
    a = _make_eligible(make_person, name='Never', gender='Woman')
    b = _make_eligible(make_person, name='Old', gender='Woman')
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO spotlight_occurrence (kind, person_id, request_key, created_at)
                VALUES ('welcome', %(id)s, 'old-suggest-key', NOW() - interval '60 days')""",
            dict(id=b['id']))
    s = client.get('/admin/growth/spotlight/suggest', headers={'Authorization': f'Bearer {tok}'}).get_json()
    ids = [x['person_id'] for x in s]
    assert a['id'] in ids and (b['id'] not in ids or ids.index(a['id']) < ids.index(b['id']))


def test_roundup_route_stores_and_serves_tile_payload(client, make_person):
    """POST /spotlight/roundup (Task 11) computes and stores the tile
    snapshot as `payload` on both platform rows; GET /queue merges it back
    into `tiles`/`count`/`countries` on the roundup rows only -- a welcome
    row from the same request run must not carry those keys. Tiles only
    populate with roundup_tiles_enabled on (Task 8); this exercises that
    path, not the count-only default covered separately below."""
    a = _make_eligible(make_person, name='RoundupTile', gender='Woman')
    with api_tx() as tx:
        rk_w = create_candidate(tx, kind='welcome', subject_person_id=a['id'], caption='c', created_by='t')
        # approve_card records subject consent on the welcome request's
        # current revision; roundup_snapshot's candidate query reads that
        # consent row (Task 8), not the dead approved_photo_uuid/
        # member_approved_at columns.
        rev_w = current_revision(tx, rk_w)
        record_consent(tx, rev_w['id'], a['id'], 'subject')
        set_setting(tx, 'roundup_tiles_enabled', 'true')
    H = {'X-Growth-Cron': 'test-cron-secret'}
    try:
        r = client.post('/admin/growth/spotlight/roundup', json={}, headers=H)
        assert r.status_code == 200
        rk = r.get_json()['request_key']
        rows = client.get('/admin/growth/queue', headers=H).get_json()
        roundup_rows = [x for x in rows if x['request_key'] == rk]
        assert len(roundup_rows) == 2
        for row in roundup_rows:
            assert 'tiles' in row and 'count' in row and 'countries' in row
            assert any(t['person_id'] == a['id'] for t in row['tiles'])
        welcome_row = next(x for x in rows if x['request_key'] == rk_w)
        assert 'tiles' not in welcome_row and 'count' not in welcome_row and 'countries' not in welcome_row
    finally:
        with api_tx() as tx:
            set_setting(tx, 'roundup_tiles_enabled', 'false')


def test_roundup_route_count_only_revision(client):
    """Task 8: the owner-decision default. With roundup_tiles_enabled off
    (its seeded value), the roundup revision created by create_candidate
    stands untouched -- empty participants, complete by definition."""
    r = client.post('/admin/growth/spotlight/roundup', json={}, headers={'X-Growth-Cron': 'test-cron-secret'})
    rk = r.get_json()['request_key']
    with api_tx() as tx:
        rev = current_revision(tx, rk)
        assert rev['participants'] == [] and consent_complete(tx, rev['id']) is True


def test_roundup_route_with_tiles_needs_every_participant(make_person, client):
    """Task 8: with roundup_tiles_enabled on, the route records every tiled
    member as a participant on the revision, and dispatch's fail-closed
    consent check refuses the card until each one consents."""
    a = _make_eligible(make_person, name='TileParticipant', gender='Woman')
    with api_tx() as tx:
        rk_w = create_candidate(tx, kind='welcome', subject_person_id=a['id'], caption='c', created_by='t')
        rev_w = current_revision(tx, rk_w)
        record_consent(tx, rev_w['id'], a['id'], 'subject')
        set_setting(tx, 'roundup_tiles_enabled', 'true')
    try:
        r = client.post('/admin/growth/spotlight/roundup', json={}, headers={'X-Growth-Cron': 'test-cron-secret'})
        rk = r.get_json()['request_key']
        with api_tx() as tx:
            rev = current_revision(tx, rk)
            assert [t['person_id'] for t in rev['participants']] == [a['id']]
            assert consent_complete(tx, rev['id']) is False
    finally:
        with api_tx() as tx:
            set_setting(tx, 'roundup_tiles_enabled', 'false')


def test_cron_header_never_resolves_a_session(client, monkeypatch):
    """I6: `_gate` checks the cron header before `_session`, so a cron call
    never opens a transaction (and never takes the api connection lock) to
    look up a bearer token it does not carry."""
    import service.api.admin.spotlight_routes as sr

    def _must_not_run():
        raise AssertionError('_session must never run for a valid cron request')

    monkeypatch.setattr(sr, '_session', _must_not_run)
    assert client.get('/admin/growth/queue', headers={'X-Growth-Cron': 'test-cron-secret'}).status_code == 200


def test_roundup_eligible_checks_every_tile_member(client, make_person):
    """F02: `eligible` now delegates to `dispatch_check`, so a roundup's
    participants come from its immutable revision (not the mutable payload
    snapshot) and the row must be a claimed, leased, processing row -- the
    dispatch worker's exact view -- not just any queue row by id. One
    participant who has since been reported blocks the whole card, because
    the card cannot be published without their face."""
    a = _make_eligible(make_person, name='TileSubject')
    reporter = make_person(name='TileReporter', gender='Man')
    with api_tx() as tx:
        set_setting(tx, 'publication_enabled', 'true')
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        rid = create_revision(tx, rk, caption='c', photo_uuid=None,
                              participants=[{'person_id': a['id'], 'photo_uuid': None}],
                              channels=['facebook', 'instagram'], created_by='t')
        attach_render(tx, rid, 'h', 'k', 'https://cdn/k.png')
        record_consent(tx, rid, a['id'], 'participant')
        tx.execute(
            "UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s",
            dict(rk=rk))
        rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall()
                if r['request_key'] == rk]
    qid, tok = rows[0]['id'], rows[0]['lease_token']
    H = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.get(f'/admin/growth/queue/{qid}/eligible?lease_token={tok}', headers=H).get_json() == {'ok': True, 'reason': ''}
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO skipped (subject_person_id, object_person_id, reported, report_reason)
               VALUES (%(a)s, %(b)s, TRUE, 'spam')""",
            dict(a=reporter['id'], b=a['id']))
    assert client.get(f'/admin/growth/queue/{qid}/eligible?lease_token={tok}', headers=H).get_json() == {
        'ok': False, 'reason': f"participant:{a['id']}:reported"}
    with api_tx() as tx:
        set_setting(tx, 'publication_enabled', 'false')


def test_eligible_without_lease_token_fails_closed(client, make_person):
    """F02: a claimed, processing row with no lease_token on the request is
    refused outright rather than told the row would pass."""
    p = _make_eligible(make_person, name='NoLease')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        tx.execute(
            "UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s",
            dict(rk=rk))
        rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall()
                if r['request_key'] == rk]
    qid = rows[0]['id']
    H = {'X-Growth-Cron': 'test-cron-secret'}
    assert client.get(f'/admin/growth/queue/{qid}/eligible', headers=H).get_json() == {
        'ok': False, 'reason': 'lease_required'}


def test_image_upload_refuses_a_row_that_moved_during_the_upload(client, monkeypatch):
    """I9: the status is read before the upload and the upload is a network
    round trip, so the write repeats the check in its own WHERE. A row that
    was approved in between keeps its old artwork and gets the same 409."""
    import base64
    import service.api.admin.spotlight_routes as sr

    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')

    def _flip(key, data):
        # Stands in for the approve that lands while the bytes are in flight.
        # Safe to open a transaction here: `_put_png` is called outside the
        # handler's own, exactly so a round trip never holds the lock.
        with api_tx() as tx:
            tx.execute("UPDATE publishing_queue SET status = 'scheduled' WHERE request_key = %(rk)s",
                       dict(rk=rk))

    monkeypatch.setattr(sr, '_put_png', _flip)
    png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
    r = client.post(f'/admin/growth/queue/{rk}/image',
                    json={'platform': 'facebook', 'png_base64': png},
                    headers={'X-Growth-Cron': 'test-cron-secret'})
    assert r.status_code == 409 and r.get_json() == {'error': 'bad_status'}
    with api_tx('read committed') as tx:
        rows = tx.execute(
            """SELECT status, image_key, image_url FROM publishing_queue
                WHERE request_key = %(rk)s""", dict(rk=rk)).fetchall()
    assert {row['status'] for row in rows} == {'scheduled'}
    assert all(row['image_key'] is None and row['image_url'] is None for row in rows)


def test_queue_row_counts_one_signup_per_person(client, make_person):
    """M-b: two clicks that lead back to the same member are one sign-up, so
    the queue view and `post_stats` report the same number. Also covers I3:
    a route-created candidate has its own campaign_link row."""
    from service.campaigns import record_click

    p = _make_eligible(make_person, name='SignupOnce')
    joiner = make_person(name='SignupJoiner')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        key = tx.execute("SELECT key FROM campaign_link WHERE kind = %(k)s",
                         dict(k=f'post:{rk}')).fetchone()['key']
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        tx.execute("UPDATE campaign_click SET signup_person_id = %(pid)s WHERE link_key = %(k)s",
                   dict(pid=joiner['id'], k=key))
    rows = client.get('/admin/growth/queue', headers={'X-Growth-Cron': 'test-cron-secret'}).get_json()
    mine = [r for r in rows if r['request_key'] == rk]
    assert len(mine) == 2
    assert all(r['clicks'] == 2 and r['signups'] == 1 for r in mine)


def test_caption_edit_keeps_exactly_one_campaign_link(client, make_person):
    """Editing the caption must not drop the row's own /s/<key> link: the
    same link is what `_Q_ROWS` counts clicks and sign-ups against, so a
    caption saved without it silently loses the post's CTA."""
    from service.config import WEB_BASE_URL

    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='New this week', created_by='t')
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
        key = tx.execute("SELECT key FROM campaign_link WHERE kind = %(k)s", dict(k=f'post:{rk}')).fetchone()['key']
    link = f"{WEB_BASE_URL.rstrip('/')}/s/{key}"

    r = client.post(f'/admin/growth/queue/{qid}/caption',
                     json={'caption': 'Brand new caption text with no link at all'}, headers=A)
    assert r.status_code == 200
    body = r.get_json()['caption']
    assert body.count(link) == 1
    assert body.endswith(link)
    with api_tx('read committed') as tx:
        stored = tx.execute("SELECT caption FROM publishing_queue WHERE id = %(id)s", dict(id=qid)).fetchone()['caption']
    assert stored == body

    # Re-editing a caption that already ends with the link must not duplicate it.
    r2 = client.post(f'/admin/growth/queue/{qid}/caption', json={'caption': body}, headers=A)
    assert r2.status_code == 200
    assert r2.get_json()['caption'].count(link) == 1


def test_queue_rows_carry_revision_and_consent_complete(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    H = {'X-Growth-Cron': 'test-cron-secret'}
    rows = [r for r in client.get('/admin/growth/queue', headers=H).get_json() if r['request_key'] == rk]
    assert len(rows) == 2
    assert all(r['revision'] == 1 for r in rows)
    # No one has consented yet, and this is a subject-bearing request, so it
    # is not complete.
    assert all(r['consent_complete'] is False for r in rows)


def test_queue_needs_render_filter_lists_unrendered_revisions(client, make_person):
    with api_tx() as tx:
        rendered_rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        _render(tx, rendered_rk)
        unrendered_rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c2', created_by='t')
    H = {'X-Growth-Cron': 'test-cron-secret'}
    rows = client.get('/admin/growth/queue?needs_render=1', headers=H).get_json()
    keys = {r['request_key'] for r in rows}
    assert unrendered_rk in keys and rendered_rk not in keys
    # The old status filter still works unchanged, for compatibility.
    still_awaiting = client.get('/admin/growth/queue?status=awaiting_render', headers=H).get_json()
    assert unrendered_rk in {r['request_key'] for r in still_awaiting}


def test_caption_route_refuses_a_scheduled_row(client, make_person):
    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        _render(tx, rk)
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall():
            set_status(tx, r['id'], 'scheduled')
    r = client.post(f'/admin/growth/queue/{qid}/caption', json={'caption': 'x'}, headers=A)
    assert r.status_code == 409 and r.get_json() == {'error': 'in_flight'}


def _terminal_caption_refused(client, make_person, terminal_status):
    """Fix round 1: a published or cancelled request is done, and its
    caption (and the revision it points at) must not be quietly rewritten
    underneath it."""
    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
        tx.execute("UPDATE publishing_queue SET status = %(st)s WHERE request_key = %(rk)s",
                   dict(st=terminal_status, rk=rk))
        before = tx.execute("SELECT caption, current_revision_id FROM publishing_queue WHERE id = %(id)s",
                            dict(id=qid)).fetchone()
    r = client.post(f'/admin/growth/queue/{qid}/caption', json={'caption': 'a whole new caption'}, headers=A)
    assert r.status_code == 409 and r.get_json() == {'error': 'terminal'}
    with api_tx('read committed') as tx:
        after = tx.execute("SELECT caption, current_revision_id FROM publishing_queue WHERE id = %(id)s",
                           dict(id=qid)).fetchone()
    assert after['caption'] == before['caption']
    assert after['current_revision_id'] == before['current_revision_id']


def test_caption_route_refuses_a_published_row(client, make_person):
    _terminal_caption_refused(client, make_person, 'published')


def test_caption_route_refuses_a_cancelled_row(client, make_person):
    _terminal_caption_refused(client, make_person, 'cancelled')


def test_growth_limit_exempts_cron_header_not_bare_ip(monkeypatch):
    """I: the /admin/growth/* admin-or-cron routes used to share unsubscribe's
    20/minute bucket, so the admin worker's per-minute claim poll could exhaust
    it before the once-a-day tick ran from the same egress IP. They now carry
    their own `growth_limit` whose `exempt_when` clears the bucket for a valid
    cron header outright (on top of the pre-existing private-IP exemption).

    The suite runs with IP-based rate limiting disabled (`test/input/
    disable-ip-rate-limit`), so `_is_private_ip` already returns True for
    every request here regardless of this fix -- looping real requests would
    pass whether or not the cron exemption exists. `_is_private_ip` is
    monkeypatched to False so the assertion actually isolates the cron-header
    behaviour, exercised through Flask's own request context rather than a
    live HTTP call."""
    import service.api.admin.spotlight_routes as sr
    import service.api.decorators as dec

    monkeypatch.setattr(sr, '_is_private_ip', lambda: False)
    app = dec.app

    with app.test_request_context('/admin/growth/settings', headers={'X-Growth-Cron': 'test-cron-secret'}):
        assert sr._growth_limit_exempt() is True

    with app.test_request_context('/admin/growth/settings'):
        assert sr._growth_limit_exempt() is False

    with app.test_request_context('/admin/growth/settings', headers={'X-Growth-Cron': 'wrong'}):
        assert sr._growth_limit_exempt() is False
