import uuid
from contextlib import contextmanager

import pytest

from database import api_tx
from service.spotlight.queue import create_candidate, set_setting
from service.spotlight.revisions import create_revision, current_revision, attach_render, record_consent
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
    """Back to the seeded defaults (migration 0044): publishing paused,
    external access allowed. Every test that flips a setting restores it in
    a `finally`, so a failing assertion mid-test cannot leave publishing
    switched on for whatever runs next in this shared database."""
    set_setting(tx, 'publication_enabled', 'false'); set_setting(tx, 'external_access_enabled', 'true')


@contextmanager
def _publication_on():
    """Publishing switched on for the duration of the block, and back to the
    seeded defaults on the way out even when an assertion fails. Opens its
    own transactions before and after the caller's, never inside one: the
    api connection lock is not reentrant."""
    with api_tx() as tx:
        _settings_on(tx)
    try:
        yield
    finally:
        with api_tx() as tx:
            _settings_off(tx)


def test_fully_valid_row_is_ok(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        for r in rows:
            assert dispatch_check(tx, r['id'], r['lease_token']) == (True, '')


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
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        r = rows[0]
        tx.execute(mutate, dict(id=r['id']))
        assert dispatch_check(tx, r['id'], r['lease_token']) == (False, reason)


def test_lease_required_and_mismatch(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        assert dispatch_check(tx, rows[0]['id'], None) == (False, 'lease_required')
        assert dispatch_check(tx, rows[0]['id'], 'f' * 32) == (False, 'lease_mismatch')


def test_deleting_chosen_photo_fails_even_with_another_approved_photo(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash) VALUES (gen_random_uuid(), %(id)s, 2, 'approved', 'x', 'y')", dict(id=p['id']))
    with _publication_on(), api_tx() as tx:
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        tx.execute("DELETE FROM photo WHERE uuid::text = %(u)s", dict(u=photo))
        ok, reason = dispatch_check(tx, rows[0]['id'], rows[0]['lease_token'])
        assert ok is False and reason == 'subject:photo_missing'


def test_hard_deleted_subject_fails_closed(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on():
        with api_tx() as tx:
            rk, rows = _claimed_subject_row(tx, p['id'], photo)
            qid, tok = rows[0]['id'], rows[0]['lease_token']
        with api_tx() as tx:
            tx.execute("DELETE FROM person WHERE id = %(id)s", dict(id=p['id']))   # FK sets subject_person_id NULL
        with api_tx() as tx:
            assert dispatch_check(tx, qid, tok) == (False, 'subject_missing')


def test_settings_gate(make_person):
    p = _make_eligible(make_person); photo = _photo(p['id'])
    with _publication_on(), api_tx() as tx:
        set_setting(tx, 'publication_enabled', 'false'); set_setting(tx, 'external_access_enabled', 'true')
        rk, rows = _claimed_subject_row(tx, p['id'], photo)
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (False, 'publication_disabled')
        set_setting(tx, 'publication_enabled', 'true'); set_setting(tx, 'external_access_enabled', 'false')
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (False, 'external_access_disabled')


def test_count_only_roundup_ok():
    with _publication_on(), api_tx() as tx:
        rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
        attach_render(tx, current_revision(tx, rk)['id'], 'h', 'k', 'https://cdn/k.png')
        tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
        rows = [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall() if r['request_key'] == rk]
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (True, '')


def _participant(pid, name, photo_uuid):
    """Exactly the shape POST /admin/growth/spotlight/roundup writes onto a
    tiled roundup's revision."""
    from service.spotlight.eligibility import photo_url
    return dict(person_id=pid, first_name=name,
                photo_url=photo_url(photo_uuid, 450) if photo_uuid else None,
                photo_uuid=photo_uuid)


def _claimed_roundup(tx, participants):
    rk = create_candidate(tx, kind='roundup', subject_person_id=None, caption='r', created_by='t')
    rid = create_revision(tx, rk, caption='r', photo_uuid=None, participants=participants,
                          channels=['facebook', 'instagram'], created_by='t')
    attach_render(tx, rid, 'h', 'k', 'https://cdn/k.png')
    for p in participants:
        record_consent(tx, rid, p['person_id'], 'participant')
    tx.execute("UPDATE publishing_queue SET status = 'scheduled', scheduled_for = NOW() - interval '1 minute' WHERE request_key = %(rk)s", dict(rk=rk))
    return [r for r in tx.execute("SELECT * FROM claim_spotlight_posts(10)").fetchall() if r['request_key'] == rk]


def test_tile_whose_own_photo_was_deleted_fails_even_with_another_approved_photo(make_person):
    """Fix wave item 3: a tile is checked against the photo the tile itself
    shows, not against whether the member happens to have some approved
    photo. Deleting the pictured one must fail the card."""
    a = _make_eligible(make_person, name='TilePhoto')
    photo = _photo(a['id'])
    with api_tx() as tx:
        tx.execute("INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash) VALUES (gen_random_uuid(), %(id)s, 2, 'approved', 'x', 'y')", dict(id=a['id']))
    with _publication_on(), api_tx() as tx:
        rows = _claimed_roundup(tx, [_participant(a['id'], 'Tile', photo)])
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (True, '')
        tx.execute("DELETE FROM photo WHERE uuid::text = %(u)s", dict(u=photo))
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (
            False, f"participant:{a['id']}:photo_missing")


def test_participant_without_a_photo_uuid_fails_closed(make_person):
    """A participant object carrying no photo_uuid is not a pass: there is
    no way to check what the tile actually shows, so the card is refused."""
    a = _make_eligible(make_person, name='TileNoUuid')
    with _publication_on(), api_tx() as tx:
        rows = _claimed_roundup(tx, [dict(person_id=a['id'], first_name='Tile', photo_url=None)])
        assert dispatch_check(tx, rows[0]['id'], rows[0]['lease_token']) == (
            False, f"participant:{a['id']}:photo_missing")
