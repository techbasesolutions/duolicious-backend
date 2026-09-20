"""/check-verification tells the client what actually happened (task 1).

The cron writes status='success' for two genuinely different results: a
selfie that matched the member's photos ('Photos'), and a selfie that
passed the anti-spoof gestures but matched nothing ('Basics only').
Q_CHECK_VERIFICATION used to select status and message only, so the web
client rendered "Approved" for both and members were told they were
verified when they were not.

These tests pin the derived `outcome` field, and pin the pre-existing
response fields so nothing downstream breaks.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _session(person_row) -> SimpleNamespace:
    # get_check_verification reads only s.person_id.
    return SimpleNamespace(
        person_id=person_row['id'], person_uuid=person_row['uuid'])


def _set_level(person_id: int, level_name: str) -> None:
    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person
               SET verification_level_id = (
                       SELECT id FROM verification_level
                        WHERE name = %(name)s)
             WHERE id = %(person_id)s
            """,
            dict(name=level_name, person_id=person_id),
        )


def _insert_job(person_id: int, status: str, message: str = '') -> int:
    from database import api_tx
    with api_tx() as tx:
        return tx.execute(
            """
            INSERT INTO verification_job (person_id, status, message, photo_uuid)
            VALUES (%(person_id)s, %(status)s, %(message)s, 'proof-uuid')
            RETURNING id
            """,
            dict(person_id=person_id, status=status, message=message),
        ).fetchone()['id']


def _check(person_row):
    from service.person import get_check_verification
    return get_check_verification(s=_session(person_row))


# ---------------------------------------------------------------------------
# The four outcomes
# ---------------------------------------------------------------------------

def test_success_with_photos_reads_photos(make_person):
    person = make_person()
    _insert_job(person['id'], 'success', '')
    _set_level(person['id'], 'Photos')

    assert _check(person)['outcome'] == 'photos'


def test_success_with_basics_only_reads_basics_only(make_person):
    """The bug. The job succeeded, the member is not photo verified."""
    person = make_person()
    _insert_job(person['id'], 'success', '')
    _set_level(person['id'], 'Basics only')

    assert _check(person)['outcome'] == 'basics_only'


def test_failure_reads_none(make_person):
    person = make_person()
    _insert_job(person['id'], 'failure', 'Your selfie was rejected')

    assert _check(person)['outcome'] == 'none'


@pytest.mark.parametrize('status', ['uploading-photo', 'queued', 'running'])
def test_unfinished_job_reads_pending(make_person, status):
    person = make_person()
    _insert_job(person['id'], status, 'Verifying')

    assert _check(person)['outcome'] == 'pending'


def test_no_job_row_reads_pending_and_does_not_error(make_person):
    person = make_person()

    row = _check(person)

    assert row['outcome'] == 'pending'
    assert row['status'] is None


# ---------------------------------------------------------------------------
# An unrecognised status is not success
# ---------------------------------------------------------------------------

def test_unrecognised_status_does_not_read_as_photos():
    """The enum could gain a value without this mapping being updated.
    A status we do not recognise must never be reported as verified."""
    from service.person import verification_outcome

    for status in ['banana', '', 'SUCCESS', None]:
        assert verification_outcome(status, 'Photos') != 'photos'
        assert verification_outcome(status, 'Photos') == 'pending'


def test_success_with_an_unexpected_level_is_not_photos():
    from service.person import verification_outcome

    assert verification_outcome('success', 'No verification') == 'basics_only'
    assert verification_outcome('success', None) == 'basics_only'


# ---------------------------------------------------------------------------
# Nothing downstream breaks
# ---------------------------------------------------------------------------

def test_pre_existing_fields_keep_their_shape_and_values(make_person):
    """Other callers read this endpoint. These six fields are the contract
    as it stood before `outcome` was added."""
    person = make_person()
    _insert_job(person['id'], 'queued', 'Waiting in line')

    row = _check(person)

    assert row['verified_gender'] is False
    assert row['verified_age'] is False
    assert row['verified_ethnicity'] is False
    assert row['verified_photos'] is None
    assert row['status'] == 'queued'
    assert row['message'] == 'Waiting in line'


def test_response_carries_the_level_and_the_tier(make_person):
    person = make_person()
    _insert_job(person['id'], 'success', '')
    _set_level(person['id'], 'Photos')

    row = _check(person)

    assert row['verification_level_name'] == 'Photos'
    assert row['ahavah_verification_tier'] == 'none'


# ---------------------------------------------------------------------------
# Multiple job rows
# ---------------------------------------------------------------------------

def test_the_newest_job_row_wins(make_person):
    """Nothing constrains verification_job to one row per person, and the
    multi selfie path deletes then inserts in a transaction that can be
    raced. The endpoint must report the most recent attempt, not an
    arbitrary one."""
    person = make_person()
    old = _insert_job(person['id'], 'failure', 'Old attempt')
    new = _insert_job(person['id'], 'queued', 'New attempt')
    assert new > old

    row = _check(person)

    assert row['status'] == 'queued'
    assert row['message'] == 'New attempt'
    assert row['outcome'] == 'pending'
