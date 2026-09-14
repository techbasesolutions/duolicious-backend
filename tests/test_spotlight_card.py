import pytest
from database import api_tx
from service.spotlight.approval import make_card_token, parse_card_token, card_url, card_state
from service.spotlight.queue import create_candidate
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


def test_token_roundtrip_and_expiry(monkeypatch):
    import service.spotlight.approval as ap
    tok = make_card_token('abc', 'A@B.co')
    assert parse_card_token(tok) == ('abc', 'a@b.co', 'ok')
    assert parse_card_token('x.y') == (None, None, 'invalid')
    monkeypatch.setattr(ap, '_now_ts', lambda: 0)
    old = make_card_token('abc', 'a@b.co')
    monkeypatch.undo()
    assert parse_card_token(old)[2] == 'expired'
    assert '/spotlight/card/' in card_url('abc', 'a@b.co')


def test_get_is_read_only_and_post_approves(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
    tok = make_card_token(rk, _email(p['id']))
    r = client.get(f'/spotlight/card/{tok}')
    assert r.status_code == 200 and r.get_json()['status'] == 'awaiting_member' and r.get_json()['photos'][0]['uuid'] == photo
    with api_tx('read committed') as tx:
        assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'awaiting_member'}
    r = client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo})
    assert r.status_code == 200
    with api_tx('read committed') as tx:
        assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'awaiting_render'}
    assert client.post(f'/spotlight/card/{tok}', json={'decision': 'approve', 'photo_uuid': photo}).get_json().get('already') is True
    assert client.get(f'/spotlight/card/{tok}').get_json()['status'] == 'approved'


def test_post_skip_cancels_and_wrong_email_is_403(client, make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
    assert client.post(f'/spotlight/card/{make_card_token(rk, "someone@else.invalid")}', json={'decision': 'skip'}).status_code == 403
    r = client.post(f'/spotlight/card/{make_card_token(rk, _email(p["id"]))}', json={'decision': 'skip'})
    assert r.status_code == 200
    with api_tx('read committed') as tx:
        assert {x['status'] for x in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()} == {'cancelled'}
    assert client.get(f'/spotlight/card/{make_card_token(rk, _email(p["id"]))}').get_json()['status'] == 'skipped'


def test_bad_tokens(client):
    assert client.get('/spotlight/card/not.a.token').status_code == 400
    assert client.post('/spotlight/card/not.a.token', json={'decision': 'skip'}).status_code == 400
    assert client.get(f'/spotlight/card/{make_card_token("nope", "a@b.co")}').status_code == 404


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
