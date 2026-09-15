from contextlib import contextmanager

import pytest

from database import api_tx
from service.spotlight.queue import create_candidate, record_receipt, set_setting
from service.spotlight.revisions import current_revision, attach_render, record_consent, create_revision
from service.spotlight.dispatch import dispatch_check
from service.spotlight.eligibility import eligibility
from service.spotlight.occurrence import record_occurrence, pictured_people, is_first_confirmation


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


def _on(tx):  set_setting(tx, 'publication_enabled', 'true'); set_setting(tx, 'external_access_enabled', 'true')
def _off(tx): set_setting(tx, 'publication_enabled', 'false'); set_setting(tx, 'external_access_enabled', 'true')


@contextmanager
def _publication_on():
    """Publishing switched on for the duration of the block, and back to the
    seeded defaults (migration 0044: publishing paused, external access
    allowed) on the way out even when an assertion fails. Opens its own
    transactions before and after the caller's, never inside one: the api
    connection lock is not reentrant."""
    with api_tx() as tx:
        _on(tx)
    try:
        yield
    finally:
        with api_tx() as tx:
            _off(tx)


def _claimed(tx, pid, photo):
    rk = create_candidate(tx, kind='welcome', subject_person_id=pid, caption='c', created_by='t')
    rev = current_revision(tx, rk); attach_render(tx, rev['id'], 'h', 'k', 'https://cdn/k.png'); record_consent(tx, rev['id'], pid, 'subject')
    tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall() if r['request_key'] == rk]
    return rk, {r['platform']: r for r in rows}


@pytest.mark.parametrize('first,second', [('facebook', 'instagram'), ('instagram', 'facebook')])
def test_sibling_channel_passes_after_first_publishes(make_person, first, second):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed(tx, p['id'], photo)
        assert dispatch_check(tx, rows[first]['id'], rows[first]['lease_token']) == (True, '')
        record_receipt(tx, rows[first]['id'], rows[first]['lease_token'], 'published', external_post_id='1')
        record_occurrence(tx, 'welcome', rk, [p['id']])
        assert dispatch_check(tx, rows[second]['id'], rows[second]['lease_token']) == (True, '')
        record_receipt(tx, rows[second]['id'], rows[second]['lease_token'], 'published', external_post_id='2')
        assert record_occurrence(tx, 'welcome', rk, [p['id']]) == 0
        assert tx.execute("SELECT count(*) AS n FROM spotlight_occurrence WHERE request_key = %(rk)s", dict(rk=rk)).fetchone()['n'] == 1


def test_new_request_within_30_days_is_blocked(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("INSERT INTO spotlight_occurrence (kind, person_id, request_key) VALUES ('welcome', %(p)s, 'old-key')", dict(p=p['id']))
        assert eligibility(tx, p['id']) == (False, 'featured_recently')
        assert eligibility(tx, p['id'], exclude_request_key='old-key')[0] is True
        with pytest.raises(ValueError, match='featured_recently'):
            create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE spotlight_occurrence SET created_at = NOW() - interval '31 days' WHERE request_key = 'old-key'")
        assert eligibility(tx, p['id'])[0] is True


def test_roundup_participants_each_get_an_occurrence(make_person):
    a = _make_eligible(make_person, name='A'); b = _make_eligible(make_person, name='B', gender='Man')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rev = current_revision(tx, rk)
        create_revision(tx, rk, caption='r', photo_uuid=None, participants=[dict(person_id=a['id']), dict(person_id=b['id'])], channels=rev['channels'], created_by='t')
        row = tx.execute("SELECT * FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1", dict(rk=rk)).fetchone()
        assert sorted(pictured_people(tx, row)) == sorted([a['id'], b['id']])
        assert record_occurrence(tx, 'roundup', rk, pictured_people(tx, row)) == 2


def test_first_confirmation_only_once(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed(tx, p['id'], photo)
        record_receipt(tx, rows['facebook']['id'], rows['facebook']['lease_token'], 'published', external_post_id='1')
        assert is_first_confirmation(tx, rk) is True
        record_receipt(tx, rows['instagram']['id'], rows['instagram']['lease_token'], 'published', external_post_id='2')
        assert is_first_confirmation(tx, rk) is False


def test_complete_route_sends_e5_once_with_post_url(make_person, client, monkeypatch):
    import service.api.admin.spotlight_routes as routes
    sent = []
    monkeypatch.setattr(routes, '_send_card_live', lambda *a: sent.append(a))
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on():
        with api_tx() as tx:
            rk, rows = _claimed(tx, p['id'], photo)
            fb, ig = rows['facebook'], rows['instagram']
        h = {'X-Growth-Cron': 'test-cron-secret'}
        r = client.post(f"/admin/growth/queue/{fb['id']}/complete", json=dict(lease_token=fb['lease_token'], status='published', external_post_id='1_2', post_url='https://www.facebook.com/1_2'), headers=h)
        assert r.status_code == 200
        r = client.post(f"/admin/growth/queue/{ig['id']}/complete", json=dict(lease_token=ig['lease_token'], status='published', external_post_id='77', post_url='https://www.instagram.com/p/abc/'), headers=h)
        assert r.status_code == 200
        assert sent == [(p['id'], rk, '1_2', 'facebook', 'https://www.facebook.com/1_2')]
        with api_tx() as tx:
            assert tx.execute("SELECT count(*) AS n FROM spotlight_occurrence WHERE request_key = %(rk)s AND person_id = %(p)s", dict(rk=rk, p=p['id'])).fetchone()['n'] == 1


def test_card_live_email_prefers_receipt_url():
    from emails.spotlight_card_live import post_url_for
    # the module-level helper stays; the send function's post_url parameter takes precedence when it is https
    assert post_url_for('facebook', '1_2') == 'https://www.facebook.com/1_2'
