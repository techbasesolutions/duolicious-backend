import pytest
from database import api_tx
from service.spotlight.queue import (create_candidate, set_member_approval, expire_member_approvals, attach_image,
                                     set_status, cancel_for_member, settings, set_setting, stamp_featured)
from service.spotlight import set_spotlight_opt_in


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


def _rows(tx, rk):
    return tx.execute("SELECT * FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform", dict(rk=rk)).fetchall()


def test_create_candidate_two_rows_awaiting_member(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='test')
        rows = _rows(tx, rk)
    assert [r['platform'] for r in rows] == ['facebook', 'instagram']
    assert {r['status'] for r in rows} == {'awaiting_member'}


def test_create_candidate_refuses_ineligible(make_person):
    p = make_person(name='NoOpt')
    with api_tx() as tx:
        with pytest.raises(ValueError) as e:
            create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='x', created_by='test')
        assert 'not_opted_in' in str(e.value) or 'not_verified' in str(e.value)


def test_roundup_has_no_subject_and_awaits_render():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='New this week', created_by='tick')
        assert {r['status'] for r in _rows(tx, rk)} == {'awaiting_render'}


def test_member_approval_then_image_then_review(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
        assert set_member_approval(tx, rk, photo) == 2
        assert {r['status'] for r in _rows(tx, rk)} == {'awaiting_render'}
        assert all(r['approved_photo_uuid'] is not None and r['member_approved_at'] is not None for r in _rows(tx, rk))
        assert attach_image(tx, rk, 'spotlight/x.png', 'https://cdn/x.png') == 2
        assert {r['status'] for r in _rows(tx, rk)} == {'review'}


def test_member_approval_rejects_foreign_photo(make_person):
    p = _make_eligible(make_person)
    other = _make_eligible(make_person, name='Other')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        foreign = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=other['id'])).fetchone()['u']
        with pytest.raises(ValueError):
            set_member_approval(tx, rk, foreign)


def test_expire_member_approvals(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET created_at = NOW() - interval '8 days' WHERE request_key = %(rk)s", dict(rk=rk))
        assert expire_member_approvals(tx, days=7) >= 2
        assert {r['status'] for r in _rows(tx, rk)} == {'cancelled'}


def test_set_status_transitions(make_person):
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        attach_image(tx, rk, 'k', 'https://cdn/k.png')
        qid = _rows(tx, rk)[0]['id']
        set_status(tx, qid, 'scheduled')
        with pytest.raises(ValueError):
            set_status(tx, qid, 'published')            # scheduled -> published not allowed (must pass processing)
        tx.execute("UPDATE publishing_queue SET status = 'processing', attempts = 3 WHERE id = %(id)s", dict(id=qid))
        set_status(tx, qid, 'failed', error='boom')
        with pytest.raises(ValueError):
            set_status(tx, qid, 'scheduled')            # attempts exhausted


def test_cancel_for_member_creates_removal_tasks(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published', external_post_id = '123' WHERE request_key = %(rk)s AND platform = 'instagram'", dict(rk=rk))
        tx.execute("UPDATE publishing_queue SET status = 'scheduled' WHERE request_key = %(rk)s AND platform = 'facebook'", dict(rk=rk))
        n = cancel_for_member(tx, p['id'], 'opt_out')
        assert n == 1
        tasks = tx.execute("SELECT t.platform, t.reason FROM spotlight_removal_task t JOIN publishing_queue q ON q.id = t.queue_id WHERE q.request_key = %(rk)s", dict(rk=rk)).fetchall()
        assert [(t['platform'], t['reason']) for t in tasks] == [('instagram', 'manual_instagram')]


def test_cancel_for_member_deletes_stored_images(make_person, monkeypatch):
    import service.spotlight.queue as q
    deleted = []
    monkeypatch.setattr(q, 'delete_images', lambda keys: deleted.extend(keys) or len(keys))
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue SET status = 'published', external_post_id = '123',
                             image_key = 'spotlight/published.png'
                       WHERE request_key = %(rk)s AND platform = 'instagram'""", dict(rk=rk))
        tx.execute("""UPDATE publishing_queue SET status = 'scheduled', image_key = 'spotlight/scheduled.png'
                       WHERE request_key = %(rk)s AND platform = 'facebook'""", dict(rk=rk))
        cancel_for_member(tx, p['id'], 'opt_out')
    assert deleted == ['spotlight/scheduled.png']


def test_opt_out_cancels(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        set_spotlight_opt_in(tx, p['id'], False)
        assert {r['status'] for r in _rows(tx, rk)} == {'cancelled'}


def test_settings_roundtrip():
    with api_tx() as tx:
        assert settings(tx)['scheduler_enabled'] in ('true', 'false')
        set_setting(tx, 'auto_welcome', 'true')
        assert settings(tx)['auto_welcome'] == 'true'
        with pytest.raises(ValueError):
            set_setting(tx, 'nope', 'true')
        set_setting(tx, 'auto_welcome', 'false')
