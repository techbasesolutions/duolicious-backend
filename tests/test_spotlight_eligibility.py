import pytest
from database import api_tx
from service.spotlight.eligibility import eligibility, primary_photo_uuid, photo_url, REASONS
from service.config import USER_IMAGES_BASE_URL


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


def test_eligible_member_passes(make_person):
    p = _make_eligible(make_person)
    with api_tx('read committed') as tx:
        assert eligibility(tx, p['id']) == (True, '')
        assert primary_photo_uuid(tx, p['id']) is not None


@pytest.mark.parametrize('sql,reason', [
    ("UPDATE person SET spotlight_opt_in = FALSE WHERE id = %(id)s", 'not_opted_in'),
    ("UPDATE person SET ahavah_verification_tier = 'none' WHERE id = %(id)s", 'not_verified'),
    ("UPDATE person SET date_of_birth = (NOW() - interval '17 years')::date WHERE id = %(id)s", 'under_18'),
    ("UPDATE person SET deletion_requested_at = NOW() WHERE id = %(id)s", 'pending_deletion'),
    ("UPDATE person SET activated = FALSE WHERE id = %(id)s", 'not_activated'),
    ("INSERT INTO spotlight_occurrence (kind, person_id, request_key) VALUES ('welcome', %(id)s, 'cooldown-test')", 'featured_recently'),
    ("DELETE FROM photo WHERE person_id = %(id)s", 'no_photo'),
])
def test_each_reason(make_person, sql, reason):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute(sql, dict(id=p['id']))
        ok, why = eligibility(tx, p['id'])
    assert (ok, why) == (False, reason)


def test_reported_member_is_ineligible(make_person):
    p = _make_eligible(make_person)
    reporter = make_person(name='Rep', gender='Man')
    with api_tx() as tx:
        tx.execute("INSERT INTO skipped (subject_person_id, object_person_id, reported, report_reason) VALUES (%(a)s, %(b)s, TRUE, 'spam')", dict(a=reporter['id'], b=p['id']))
        assert eligibility(tx, p['id']) == (False, 'reported')


def test_reason_order_is_stable(make_person):
    p = _make_eligible(make_person)
    with api_tx() as tx:
        tx.execute("UPDATE person SET spotlight_opt_in = FALSE, ahavah_verification_tier = 'none' WHERE id = %(id)s", dict(id=p['id']))
        assert eligibility(tx, p['id'])[1] == 'not_opted_in'
    assert REASONS.index('not_opted_in') < REASONS.index('not_verified')


def test_photo_url():
    assert photo_url('abc') == f"{USER_IMAGES_BASE_URL}/original-abc.jpg"
    assert photo_url('abc', 450) == f"{USER_IMAGES_BASE_URL}/450-abc.jpg"
