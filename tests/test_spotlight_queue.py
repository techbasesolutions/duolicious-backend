import json

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


def test_create_candidate_appends_a_campaign_link_to_the_caption(make_person):
    """I3: every card's caption carries its own /s/ link, minted once per
    request in create_candidate so both platform rows share it and a
    hand-written caption gets one too."""
    from service.campaigns import record_click
    from service.config import WEB_BASE_URL
    from service.growth.queries import post_stats

    p = _make_eligible(make_person, name='Linked')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'],
                              caption='Welcome to Ahavah, Linked.', created_by='t')
        captions = {r['caption'] for r in _rows(tx, rk)}
        assert len(captions) == 1
        caption = captions.pop()
        assert caption.startswith('Welcome to Ahavah, Linked. ')
        assert ' https://' in caption
        assert f" {WEB_BASE_URL.rstrip('/')}/s/" in caption

        key = caption.rsplit('/', 1)[1]
        link = tx.execute("SELECT kind FROM campaign_link WHERE key = %(k)s", dict(k=key)).fetchone()
        assert link['kind'] == f'post:{rk}'

        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        assert post_stats(tx, rk) == {'clicks': 1, 'signups': 0}


def _tile_payload(person_id, first_name):
    return json.dumps(dict(
        tiles=[dict(person_id=person_id, first_name=first_name, photo_url='https://cdn/t.jpg')],
        count=1, countries=1))


def test_opt_out_cancels_a_roundup_that_tiles_the_member(make_person):
    """C1: a roundup carries no subject_person_id, so the subject sweep cannot
    reach it -- but its stored tile snapshot shows this member's photo, so
    withdrawn consent has to cancel it all the same. Another member's roundup
    is left alone."""
    p = _make_eligible(make_person, name='Tiled')
    other = _make_eligible(make_person, name='Untiled')
    with api_tx() as tx:
        mine = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        theirs = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET payload = %(pl)s::jsonb WHERE request_key = %(rk)s",
                   dict(pl=_tile_payload(p['id'], 'Tiled'), rk=mine))
        tx.execute("UPDATE publishing_queue SET payload = %(pl)s::jsonb WHERE request_key = %(rk)s",
                   dict(pl=_tile_payload(other['id'], 'Untiled'), rk=theirs))
        set_spotlight_opt_in(tx, p['id'], False)
        cancelled = _rows(tx, mine)
        untouched = _rows(tx, theirs)
    assert {r['status'] for r in cancelled} == {'cancelled'}
    assert {r['error'] for r in cancelled} == {'tile_member_opt_out'}
    assert {r['status'] for r in untouched} == {'awaiting_render'}


def test_opt_out_leaves_a_published_roundup_for_the_removal_path(make_person):
    """Published rows are owned by the retention sweep or a removal task, not
    by cancel_for_member's status sweep, whichever way the member is on the
    card. The task itself is covered by the test below."""
    p = _make_eligible(make_person, name='TiledLive')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue SET payload = %(pl)s::jsonb, status = 'published'
                       WHERE request_key = %(rk)s""",
                   dict(pl=_tile_payload(p['id'], 'TiledLive'), rk=rk))
        set_spotlight_opt_in(tx, p['id'], False)
        rows = _rows(tx, rk)
    assert {r['status'] for r in rows} == {'published'}


def test_opt_out_files_removal_tasks_for_a_published_roundup(make_person):
    """C3: a live roundup that tiles this member shows their photo just as a
    live card of their own does, so it earns the same removal task, with the
    same two reason strings the admin worker already acts on. The row stays
    `published` until the platform post is actually gone."""
    p = _make_eligible(make_person, name='TiledPublishedTask')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='c', created_by='t')
        tx.execute("""UPDATE publishing_queue
                         SET payload = %(pl)s::jsonb, status = 'published',
                             external_post_id = 'post-' || platform
                       WHERE request_key = %(rk)s""",
                   dict(pl=_tile_payload(p['id'], 'TiledPublishedTask'), rk=rk))
        set_spotlight_opt_in(tx, p['id'], False)
        rows = _rows(tx, rk)
        tasks = tx.execute("""SELECT t.platform, t.external_post_id, t.reason
                                FROM spotlight_removal_task t
                                JOIN publishing_queue q ON q.id = t.queue_id
                               WHERE q.request_key = %(rk)s
                               ORDER BY t.platform""", dict(rk=rk)).fetchall()
    assert {r['status'] for r in rows} == {'published'}
    assert [(t['platform'], t['external_post_id'], t['reason']) for t in tasks] == [
        ('facebook', 'post-facebook', 'delete_via_api'),
        ('instagram', 'post-instagram', 'manual_instagram'),
    ]
