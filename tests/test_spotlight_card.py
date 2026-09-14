import pytest
from database import api_tx
from service.spotlight.approval import make_card_token, parse_card_token, card_url, card_state
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import current_revision, attach_render
from service.spotlight.withdrawal import withdraw_member
from emails.spotlight_card_ready import card_ready_html, SUBJECT as S4
from emails.spotlight_card_live import card_live_html, share_url_for, post_url_for, SUBJECT as S5


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


def _email(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=pid)).fetchone()['email']


def test_token_roundtrip_and_expiry(monkeypatch, make_person):
    import service.spotlight.approval as ap
    p = make_person(name='TokRT')
    email = _email(p['id'])
    with api_tx() as tx:
        tok = make_card_token(tx, 'abc', email.upper())
    rk, em, nonce, status = parse_card_token(tok)
    assert rk == 'abc' and em == email.lower() and status == 'ok' and nonce
    assert parse_card_token('x.y') == (None, None, None, 'invalid')
    monkeypatch.setattr(ap, '_now_ts', lambda: 0)
    with api_tx() as tx:
        old = make_card_token(tx, 'abc', email)
    monkeypatch.undo()
    assert parse_card_token(old)[3] == 'expired'
    with api_tx() as tx:
        assert '/spotlight/card/' in card_url(tx, 'abc', email)


def test_get_is_read_only_and_post_approves(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
            attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        email = _email(p['id'])
        with api_tx() as tx:
            tok = make_card_token(tx, rk, email)
        r = client.get(f'/spotlight/card/{tok}')
        body = r.get_json()
        assert r.status_code == 200 and body['status'] == 'awaiting_member' and body['photos'][0]['uuid'] == photo
        assert body['revision'] == 1 and body['preview_available'] is True and body['image_url'] == 'https://cdn/k.png'
        assert body['photo_uuid'] == photo and body['stale'] is False
        with api_tx('read committed') as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'awaiting_member'}
        r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo})
        assert r.status_code == 200 and r.get_json()['result'] == 'approved'
        with api_tx('read committed') as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'review'}
        # The nonce is now single-use: a repeat with the same token no
        # longer re-runs approve_card, it reports the current state.
        r2 = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo}).get_json()
        assert r2 == dict(ok=True, already=True, status='approved')
        assert client.get(f'/spotlight/card/{tok}').get_json()['status'] == 'approved'
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_post_approve_is_gated_on_settings_and_render(client, make_person):
    """Route-level coverage of the two new 409s: approvals off (the owner
    decision in force for this wave), and approvals on but nothing rendered
    yet. Both return a JSON {"error": "<reason>"} body -- never a plain-text
    abort -- per Task 2 fix round 1."""
    p = _make_eligible(make_person)
    email = _email(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
        tok1 = make_card_token(tx, rk, email)
    r = client.post(f'/spotlight/card/{tok1}', json={'decision': 'approve', 'photo_uuid': photo})
    assert r.status_code == 409 and r.get_json() == {'error': 'approvals_disabled'}
    # A failed attempt still burns the nonce (Wave 1 F11: it is single-use
    # once the state check passes), so the second attempt needs its own
    # fresh token rather than replaying tok1.
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
        tok2 = make_card_token(tx, rk, email)
    try:
        r = client.post(f'/spotlight/card/{tok2}', json={'decision': 'approve', 'photo_uuid': photo})
        assert r.status_code == 409 and r.get_json() == {'error': 'preview_unavailable'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_post_skip_cancels_and_wrong_email_is_403(client, make_person):
    p = _make_eligible(make_person)
    other = make_person(name='Other')
    other_email = _email(other['id'])
    email = _email(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        wrong_tok = make_card_token(tx, rk, other_email)
    assert client.post(f'/spotlight/card/{wrong_tok}', json={'decision': 'skip'}).status_code == 403
    with api_tx() as tx:
        tok = make_card_token(tx, rk, email)
    r = client.post(f'/spotlight/card/{tok}', json={'decision': 'skip'})
    assert r.status_code == 200
    with api_tx('read committed') as tx:
        assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'cancelled'}
    with api_tx() as tx:
        get_tok = make_card_token(tx, rk, email)
    assert client.get(f'/spotlight/card/{get_tok}').get_json()['status'] == 'skipped'


def test_bad_tokens(client, make_person):
    assert client.get('/spotlight/card/not.a.token').status_code == 400
    assert client.post('/spotlight/card/not.a.token', json={'decision': 'skip'}).status_code == 400
    p = make_person(name='NoSuchRequest')
    email = _email(p['id'])
    with api_tx() as tx:
        tok = make_card_token(tx, 'nope', email)
    assert client.get(f'/spotlight/card/{tok}').status_code == 404


def test_card_token_replay_after_withdrawal_is_rejected(make_person, client):
    p = _make_eligible(make_person)
    photo = None
    with api_tx() as tx:
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
        set_setting(tx, 'approvals_enabled', 'true')
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        email = tx.execute("SELECT email FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()['email']
        token = make_card_token(tx, rk, email)
    try:
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo))
        assert r.status_code == 200 and r.get_json()['ok'] is True
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo))
        assert r.status_code == 200 and r.get_json()['already'] is True
        with api_tx() as tx:
            withdraw_member(tx, p['id'], 'opt_out')
        r = client.post(f'/spotlight/card/{token}', json=dict(decision='approve', photo_uuid=photo))
        assert r.status_code == 410 and r.get_json() == dict(error='stale')
        with api_tx() as tx:
            assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'cancelled'}
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_e4_and_e5_html_are_on_template_and_escaped():
    em_dash = chr(0x2014)  # built at runtime so the source file carries no literal em dash
    h = card_ready_html('Ri<b>vka', 'member of the week', 'https://ahavah.app/spotlight/card/t', 7, 'https://ahavah.app/u/x')
    assert 'title-card-ready.png' in h and 'Ri&lt;b&gt;vka' in h and '<b>vka' not in h and em_dash not in h and em_dash not in S4
    h = card_live_html('Sarah', 'https://cdn/x.png', 'https://www.facebook.com/1', share_url_for('https://www.facebook.com/1'), 'https://ahavah.app/u/x')
    assert 'https://cdn/x.png' in h and 'sharer.php?u=https%3A%2F%2Fwww.facebook.com%2F1' in h and em_dash not in S5
    with pytest.raises(ValueError):
        card_live_html('S', 'http://insecure/x.png', 'https://a', 'https://b', 'https://u')
    assert post_url_for('facebook', '123_456') == 'https://www.facebook.com/123_456'


def test_send_card_ready_uses_runner_exempt(make_person, monkeypatch):
    import service.campaigns.runner as r
    sent = []
    class _S:
        def send(self, **kw): sent.append(kw); return 'mid'
    monkeypatch.setattr(r, 'make_aws_smtp', lambda: _S())
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'card-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    from emails.spotlight_card_ready import send_card_ready
    assert send_card_ready(p['id'], rk) is True
    assert len(sent) == 1 and '/spotlight/card/' in sent[0]['body']
    assert send_card_ready(p['id'], rk) is False      # same campaign id, idempotent


def test_send_card_live_wraps_share_link_and_is_idempotent(make_person, monkeypatch):
    import service.campaigns.runner as r
    sent = []
    class _S:
        def send(self, **kw): sent.append(kw); return 'mid'
    monkeypatch.setattr(r, 'make_aws_smtp', lambda: _S())
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'facebook'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
    from emails.spotlight_card_live import send_card_live
    assert send_card_live(p['id'], rk, '123_456', 'facebook') is True
    assert len(sent) == 1
    body = sent[0]['body']
    assert '/s/' in body and 'https://www.facebook.com/123_456' in body
    assert send_card_live(p['id'], rk, '123_456', 'facebook') is False   # same campaign id, idempotent


def test_send_card_live_prefers_a_supplied_https_post_url(make_person, monkeypatch):
    """Task 6: the worker's own receipt (Instagram's permalink lookup, in
    particular) may already carry the real post URL; when it is https, it
    wins over `post_url_for`'s Instagram-profile fallback."""
    import service.campaigns.runner as r
    sent = []
    class _S:
        def send(self, **kw): sent.append(kw); return 'mid'
    monkeypatch.setattr(r, 'make_aws_smtp', lambda: _S())
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-ig-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
    from emails.spotlight_card_live import send_card_live
    assert send_card_live(p['id'], rk, '77', 'instagram', post_url='https://www.instagram.com/p/abc/') is True
    body = sent[0]['body']
    assert 'https://www.instagram.com/p/abc/' in body
    assert 'https://www.instagram.com/ahavah.app/' not in body


def test_send_card_live_falls_back_when_post_url_is_not_https(make_person, monkeypatch):
    import service.campaigns.runner as r
    sent = []
    class _S:
        def send(self, **kw): sent.append(kw); return 'mid'
    monkeypatch.setattr(r, 'make_aws_smtp', lambda: _S())
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET email = %(e)s WHERE id = %(id)s", dict(e=f'live-ig2-{p["id"]}@ahavah-test.invalid', id=p['id']))
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute(
            "UPDATE publishing_queue SET image_url = %(u)s WHERE request_key = %(rk)s AND platform = 'instagram'",
            dict(u='https://cdn.ahavah.app/spotlight/x.png', rk=rk))
    from emails.spotlight_card_live import send_card_live, post_url_for
    assert send_card_live(p['id'], rk, '77', 'instagram', post_url='not-a-url') is True
    body = sent[0]['body']
    assert post_url_for('instagram', '77') in body
