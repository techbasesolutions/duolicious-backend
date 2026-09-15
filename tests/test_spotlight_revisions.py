import pytest, uuid
from database import api_tx
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import (create_revision, current_revision, attach_render, record_consent,
                                         consent_complete, edit_caption, approve_card)


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


def _statuses(rk):
    with api_tx('read committed') as tx:
        return {r['status'] for r in tx.execute("SELECT status FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()}


def test_candidate_creates_revision_one(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
        rev = current_revision(tx, rk)
        rows = tx.execute("SELECT current_revision_id FROM publishing_queue WHERE request_key = %(rk)s", dict(rk=rk)).fetchall()
    assert rev['revision'] == 1 and rev['asset_hash'] is None and rev['photo_uuid'] is not None
    assert all(r['current_revision_id'] == rev['id'] for r in rows)


def test_caption_edit_creates_new_revision_and_drops_consent(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='Welcome', created_by='t')
            rev1 = current_revision(tx, rk)
            attach_render(tx, rev1['id'], 'hash1', 'k1', 'https://cdn/k1.png')
            assert approve_card(tx, rk, p['id'], photo) == 'approved'
            assert consent_complete(tx, rev1['id']) is True
            rev2_id = edit_caption(tx, rk, 'Welcome, changed', 't')
            assert rev2_id != rev1['id']
            assert consent_complete(tx, rev2_id) is False
            assert consent_complete(tx, rev1['id']) is True          # old consent untouched but no longer current
            assert current_revision(tx, rk)['id'] == rev2_id
            with pytest.raises(ValueError, match='preview_unavailable'):
                approve_card(tx, rk, p['id'], photo)                 # revision 2 not rendered yet
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_edit_of_in_flight_row_is_refused(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='New this week', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='in_flight'):
            edit_caption(tx, rk, 'x', 't')


def test_approvals_disabled_by_default(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        with pytest.raises(ValueError, match='approvals_disabled'):
            approve_card(tx, rk, p['id'], photo)


def test_different_photo_makes_new_revision_without_consent(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash) VALUES (gen_random_uuid(), %(id)s, 2, 'approved', 'x', 'y')", dict(id=p['id']))
        second = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s AND position = 2", dict(id=p['id'])).fetchone()['u']
    # adapt the INSERT to the real NOT NULL photo columns recorded in the Phase B task-2 report
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            rev1 = current_revision(tx, rk)
            attach_render(tx, rev1['id'], 'h', 'k', 'https://cdn/k.png')
            assert approve_card(tx, rk, p['id'], second) == 'new_revision'
            rev2 = current_revision(tx, rk)
            assert rev2['id'] != rev1['id'] and rev2['photo_uuid'] == second and rev2['asset_hash'] is None
            assert consent_complete(tx, rev2['id']) is False
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_welcome_consent_does_not_satisfy_roundup(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'approvals_enabled', 'true')
    try:
        with api_tx() as tx:
            rk_w = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            attach_render(tx, current_revision(tx, rk_w)['id'], 'h', 'k', 'https://cdn/k.png')
            assert approve_card(tx, rk_w, p['id'], photo) == 'approved'
            rk_r = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
            rid = create_revision(tx, rk_r, caption='r', photo_uuid=None,
                                  participants=[dict(person_id=p['id'], first_name='Elig',
                                                     photo_url=f'https://img/450-{photo}.jpg',
                                                     photo_uuid=photo)],
                                  channels=['facebook','instagram'], created_by='t')
            assert consent_complete(tx, rid) is False
            assert record_consent(tx, rid, p['id'], 'participant') is True
            assert consent_complete(tx, rid) is True
    finally:
        with api_tx() as tx:
            set_setting(tx, 'approvals_enabled', 'false')


def test_count_only_roundup_is_consent_complete():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        assert consent_complete(tx, current_revision(tx, rk)['id']) is True


def test_attach_render_is_one_shot():
    with api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        rid = current_revision(tx, rk)['id']
        attach_render(tx, rid, 'h', 'k', 'https://cdn/k.png')
        with pytest.raises(ValueError, match='already_rendered'):
            attach_render(tx, rid, 'h2', 'k2', 'https://cdn/k2.png')


def test_attach_render_raises_not_found_for_a_missing_revision():
    with api_tx() as tx:
        with pytest.raises(ValueError, match='not_found'):
            attach_render(tx, 999999999, 'h', 'k', 'https://cdn/k.png')


def test_create_revision_refuses_a_terminal_request(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
        tx.execute("UPDATE publishing_queue SET status = 'published' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='terminal'):
            edit_caption(tx, rk, 'x', 't')
        tx.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE request_key = %(rk)s", dict(rk=rk))
        with pytest.raises(ValueError, match='terminal'):
            edit_caption(tx, rk, 'x', 't')
