"""service/spotlight/roundup.py: the weekly roundup tile snapshot (Task 11,
plan spec 3.3 / 3.5 E2). `_make_eligible` copied from tests/test_spotlight_queue.py
on purpose -- test files in this suite do not import from each other."""
from __future__ import annotations

from database import api_tx
from service.spotlight.queue import create_candidate
from service.spotlight.roundup import roundup_snapshot


def _stamp_member_approval(tx, request_key, photo_uuid):
    """set_member_approval (removed in Task 2, replaced by revision-bound
    approve_card) used to stamp these two columns; roundup_snapshot's
    candidate query still reads them as-is (Task 8 rewires the roundup path
    onto revisions), so this stamps them directly to keep exercising the
    tile-snapshot path this task does not touch."""
    tx.execute(
        """UPDATE publishing_queue SET approved_photo_uuid = %(u)s::uuid, member_approved_at = NOW()
            WHERE request_key = %(rk)s""",
        dict(u=photo_uuid, rk=request_key))


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


def test_snapshot_lists_only_approved_newcomers(make_person):
    a = _make_eligible(make_person, name='Approved', gender='Woman')
    b = _make_eligible(make_person, name='NotApproved', gender='Woman')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=a['id'], caption='c', created_by='t')
        photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=a['id'])).fetchone()['u']
        _stamp_member_approval(tx, rk, photo)
        create_candidate(tx, kind='welcome', subject_person_id=b['id'], caption='c', created_by='t')
        tx.execute("UPDATE person SET country = 'GB' WHERE id = %(id)s", dict(id=a['id']))
        tx.execute("UPDATE person SET country = 'US' WHERE id = %(id)s", dict(id=b['id']))
        snap = roundup_snapshot(tx, days=7)
    names = [t['first_name'] for t in snap['tiles']]
    assert 'Approved' in names and 'NotApproved' not in names
    assert snap['count'] >= 2 and snap['countries'] >= 2
    assert all(t['photo_url'].endswith('.jpg') for t in snap['tiles'])


def test_snapshot_caps_tiles_at_four(make_person):
    # `_make_eligible` opens its own api_tx (via the make_person fixture and
    # its own UPDATE/INSERT), so every person must be created BEFORE the
    # transaction below opens -- the shared api connection lock is not
    # reentrant, and calling `_make_eligible` while already inside a `with
    # api_tx()` block deadlocks the whole test process (see task-11-report.md).
    people = [_make_eligible(make_person, name=f'T{i}', gender='Woman') for i in range(5)]
    with api_tx() as tx:
        for p in people:
            rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'], caption='c', created_by='t')
            photo = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['u']
            _stamp_member_approval(tx, rk, photo)
        assert len(roundup_snapshot(tx, days=7)['tiles']) <= 4
