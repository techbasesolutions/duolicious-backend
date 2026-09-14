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
from service.spotlight.queue import create_candidate, attach_image, set_status, set_setting, set_member_approval


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
        attach_image(tx, rk, 'k', 'https://cdn/k.png')
        set_setting(tx, 'scheduler_enabled', 'true')
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall():
            set_status(tx, r['id'], 'scheduled')
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    H = {'X-Growth-Cron': 'test-cron-secret'}
    claimed = client.post('/admin/growth/queue/claim', json={'max': 5}, headers=H).get_json()
    mine = [r for r in claimed if r['request_key'] == rk]
    assert len(mine) == 2 and all(r['status'] == 'processing' for r in mine)
    r = client.post(f"/admin/growth/queue/{mine[0]['id']}/complete", json={'status': 'published', 'external_post_id': '123'}, headers=H)
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
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk_due)).fetchall():
            attach_image(tx, rk_due, 'k', 'https://cdn/k.png')
            set_status(tx, r['id'], 'scheduled')
        tx.execute("UPDATE publishing_queue SET scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk_due))
        rk_future = create_candidate(tx, kind='roundup', subject_person_id=None, caption='future', created_by='t')
        for r in tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk_future)).fetchall():
            attach_image(tx, rk_future, 'k2', 'https://cdn/k2.png')
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
    # attach_image stamps one url across the whole request key; each row must
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


def test_admin_approve_default_slot_and_purge(client, make_person):
    admin = _make_admin(make_person); tok = _session_for(admin)
    A = {'Authorization': f'Bearer {tok}'}
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        attach_image(tx, rk, 'k', 'https://cdn/k.png')
        qid = tx.execute("SELECT id FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()['id']
    r = client.post(f'/admin/growth/queue/{qid}/approve', json={'scheduled_for': None}, headers=A)
    assert r.status_code == 200 and r.get_json()['status'] == 'scheduled' and r.get_json()['scheduled_for'] is not None
    r = client.post('/admin/growth/queue/purge', json={}, headers=A)
    assert r.status_code == 200 and r.get_json()['cancelled'] >= 1
    with api_tx('read committed') as tx:
        n = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action IN ('growth.queue.approve','growth.queue.purge')").fetchone()['n']
    assert n >= 2


def test_admin_with_cron_header_still_audits(client, make_person):
    """The cron header must never launder a human mutation past the audit log:
    `_audit` keys on the session, not on the header."""
    admin = _make_admin(make_person); tok = _session_for(admin)
    headers = {'Authorization': f'Bearer {tok}', 'X-Growth-Cron': 'test-cron-secret'}
    with api_tx('read committed') as tx:
        before = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action = 'growth.queue.purge'").fetchone()['n']
    assert client.post('/admin/growth/queue/purge', json={}, headers=headers).status_code == 200
    with api_tx('read committed') as tx:
        row = tx.execute(
            """SELECT actor_email FROM admin_audit_log
                WHERE action = 'growth.queue.purge' ORDER BY created_at DESC LIMIT 1""").fetchone()
        after = tx.execute("SELECT count(*) AS n FROM admin_audit_log WHERE action = 'growth.queue.purge'").fetchone()['n']
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
        tx.execute("UPDATE person SET spotlight_last_featured_at = NOW() - interval '60 days' WHERE id = %(id)s", dict(id=b['id']))
    s = client.get('/admin/growth/spotlight/suggest', headers={'Authorization': f'Bearer {tok}'}).get_json()
    ids = [x['person_id'] for x in s]
    assert a['id'] in ids and (b['id'] not in ids or ids.index(a['id']) < ids.index(b['id']))


def test_roundup_route_stores_and_serves_tile_payload(client, make_person):
    """POST /spotlight/roundup (Task 11) computes and stores the tile
    snapshot as `payload` on both platform rows; GET /queue merges it back
    into `tiles`/`count`/`countries` on the roundup rows only -- a welcome
    row from the same request run must not carry those keys."""
    a = _make_eligible(make_person, name='RoundupTile', gender='Woman')
    with api_tx() as tx:
        rk_w = create_candidate(tx, kind='welcome', subject_person_id=a['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=a['id'])).fetchone()['u']
        set_member_approval(tx, rk_w, photo)
    H = {'X-Growth-Cron': 'test-cron-secret'}
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
