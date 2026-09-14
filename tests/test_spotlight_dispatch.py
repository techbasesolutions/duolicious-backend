import uuid, pytest
from database import api_tx
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import current_revision, attach_render, record_consent
from service.spotlight.dispatch import dispatch_check


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


def _claimed_subject_row(tx, pid, photo):
    rk = create_candidate(tx, kind='welcome', subject_person_id=pid, caption='c', created_by='t')
    rev = current_revision(tx, rk)
    attach_render(tx, rev['id'], 'h', 'k', 'https://cdn/k.png')
    record_consent(tx, rev['id'], pid, 'subject')
    tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    rows = tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall()
    mine = [r for r in rows if r['request_key'] == rk]
    return rk, mine


def _settings_on(tx):
    set_setting(tx, 'publication_enabled', 'true'); set_setting(tx, 'external_access_enabled', 'true')


def _settings_off(tx):
    set_setting(tx, 'publication_enabled', 'false'); set_setting(tx, 'external_access_enabled', 'true')


def test_fully_valid_row_is_ok(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        _settings_on(tx)
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        for r in rows:
            assert dispatch_check(tx, r['id'], r['lease_token']) == (True, '')
        _settings_off(tx)


@pytest.mark.parametrize('mutate,reason', [
    ("UPDATE publishing_queue SET status = 'review' WHERE id = %(id)s", 'not_processing'),
    ("UPDATE publishing_queue SET lease_until = NOW() - interval '1 minute' WHERE id = %(id)s", 'lease_expired'),
    ("UPDATE publishing_queue SET cancellation_requested_at = NOW() WHERE id = %(id)s", 'withdrawn'),
    ("UPDATE publishing_queue SET current_revision_id = NULL WHERE id = %(id)s", 'no_revision'),
    ("UPDATE spotlight_revision SET asset_hash = NULL WHERE id = (SELECT current_revision_id FROM publishing_queue WHERE id = %(id)s)", 'not_rendered'),
    ("DELETE FROM spotlight_revision_consent WHERE revision_id = (SELECT current_revision_id FROM publishing_queue WHERE id = %(id)s)", 'consent_incomplete'),
    ("UPDATE publishing_queue SET subject_person_id = NULL WHERE id = %(id)s", 'subject_missing'),
])
def test_each_condition_fails_closed(make_person, mutate, reason):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        _settings_on(tx)
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        r = rows[0]
        tx.execute(mutate, dict(id=r['id']))
        assert dispatch_check(tx, r['id'], r['lease_token']) == (False, reason)
        _settings_off(tx)


def test_lease_required_and_mismatch(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        _settings_on(tx)
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        assert dispatch_check(tx, rows[0]['id'], None) == (False, 'lease_required')
        assert dispatch_check(tx, rows[0]['id'], 'f' * 32) == (False, 'lease_mismatch')
        _settings_off(tx)


def test_deleting_chosen_photo_fails_even_with_another_approved_photo(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash) VALUES (gen_random_uuid(), %(id)s, 2, 'approved', 'x', 'y')", dict(id=p['id']))
    with api_tx() as tx:
        _settings_on(tx)
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        tx.execute("DELETE FROM photo WHERE uuid::text = %(u)s", dict(u=photo))
        ok, reason = dispatch_check(tx, rows[0]['id'], rows[0]['lease_token'])
        assert ok is False and reason == 'subject:photo_missing'
        _settings_off(tx)


def test_hard_deleted_subject_fails_closed(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        _settings_on(tx)
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        qid, tok = rows[0]['id'], rows[0]['lease_token']
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %(id)s", dict(id=p['id']))   # FK sets subject_person_id NULL
    with api_tx() as tx:
        assert dispatch_check(tx, qid, tok) == (False, 'subject_missing')
        _settings_off(tx)


def test_settings_gate(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        set_setting(tx, 'publication_enabled', 'false'); set_setting(tx, 'external_access_enabled', 'true')
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (False, 'publication_disabled')
        set_setting(tx, 'publication_enabled', 'true'); set_setting(tx, 'external_access_enabled', 'false')
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (False, 'external_access_disabled')
        _settings_off(tx)


def test_count_only_roundup_ok():
    with api_tx() as tx:
        _settings_on(tx)
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
        rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall() if r['request_key'] == rk]
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (True, '')
        _settings_off(tx)
