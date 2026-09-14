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
from service.spotlight.queue import create_candidate, attach_image, set_status, set_setting


def _session_for(p) -> str:
    """Mint a real duo_session row and return its bearer token."""
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s",
                           dict(i=p['id'])).fetchone()['email']
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')
            """,
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=p['id']),
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
        assert {r['status'] for r in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'review'}
    bad = base64.b64encode(b'notpng').decode()
    assert client.post(f'/admin/growth/queue/{rk}/image', json={'platform': 'facebook', 'png_base64': bad}, headers=H).status_code == 400


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
