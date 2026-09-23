"""The verification cron tells the member what happened (task 3).

Two of the four outcomes used to leave the member with nothing. A selfie
that passed the anti-spoof gestures but matched none of the member's photos
wrote 'Basics only' and said nothing on purpose: the comment in the runner
claimed there was "no clear pass/fail to report". A classifier rejection
pushed a line and, when push could not reach the member, said nothing at
all. Either way the member sat on a screen that never resolved.

These tests pin that every outcome notifies exactly once through the same
notify() path the granted tiers use, that the member's verification
preference still gates it, that a rejection never asserts a reason the
classifier did not give us, and that a notifier which blows up cannot cost
the member their verification result.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

import service.cron.verificationjobrunner as runner
import service.notifications as notifications
from database import api_tx
from verification import Failure, Success, VerificationResult


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _queue_job(person_id: int, *, silver_burst_uuids=None,
               claimed_uuids=None) -> runner.VerificationJob:
    proof = f'proof-{uuid4()}'
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO verification_job (person_id, status, message, photo_uuid)
            VALUES (%(person_id)s, 'queued', '', %(proof)s)
            RETURNING id
            """,
            dict(person_id=person_id, proof=proof),
        ).fetchone()
    return runner.VerificationJob(
        id=row['id'],
        person_id=person_id,
        proof_uuid=proof,
        claimed_uuids=list(claimed_uuids or []),
        claimed_age=30,
        claimed_gender='Man',
        claimed_ethnicity=None,
        silver_burst_uuids=silver_burst_uuids,
    )


def _matched(verified_uuids) -> VerificationResult:
    return VerificationResult(
        success=Success(
            verified_uuids=list(verified_uuids),
            is_verified_age=True,
            is_verified_gender=True,
            is_verified_ethnicity=False,
            raw_json='{}',
        ),
        failure=None,
    )


REJECTION_REASON = 'Our AI thinks you are not smiling.'


def _rejected() -> VerificationResult:
    return VerificationResult(
        success=None,
        failure=Failure(reason=REJECTION_REASON, raw_json='{}'),
    )


def _run(monkeypatch, job, result, *, notify=None) -> list[dict]:
    """Run the job with a stubbed classifier and a recording notifier.
    Returns one dict per notify() call."""
    async def fake_verify(**_kwargs):
        return result

    monkeypatch.setattr(runner, 'verify', fake_verify)

    calls: list[dict] = []

    def record(person_id, event_kind, **kw):
        calls.append(dict(person_id=person_id, event_kind=event_kind, **kw))

    monkeypatch.setattr(notifications, 'notify', notify or record)

    # Nothing in this task may reach the push helper directly: every branch
    # goes through notify(), which owns the push-or-email decision. A direct
    # push lands in the same list, so the per-branch "exactly one
    # notification, through notify()" assertions catch it. pytest.fail here
    # would be swallowed by the runner's own try/except.
    def direct_push(*_a, **kw):
        calls.append(dict(event_kind='direct push, bypassing notify', **kw))

    monkeypatch.setattr(notifications, 'send_to_user_safe', direct_push)

    asyncio.run(runner.do_verification_job(job))
    return calls


def _job_row(job_id: int) -> dict:
    with api_tx() as tx:
        return tx.execute(
            'SELECT status, message FROM verification_job WHERE id = %(id)s',
            dict(id=job_id),
        ).fetchone()


def _level_name(person_id: int):
    with api_tx() as tx:
        row = tx.execute(
            """
            SELECT vl.name AS name
              FROM person
              LEFT JOIN verification_level AS vl
                     ON vl.id = person.verification_level_id
             WHERE person.id = %(id)s
            """,
            dict(id=person_id),
        ).fetchone()
    return row['name']


# ---------------------------------------------------------------------------
# The two outcomes that used to be silent
# ---------------------------------------------------------------------------

def test_basics_only_queues_exactly_one_notification(monkeypatch, make_person):
    """Gestures passed, no profile photo matched. The member earns no tier,
    which is exactly why they need to be told."""
    person = make_person()
    job = _queue_job(person['id'])

    calls = _run(monkeypatch, job, _matched([]))

    assert len(calls) == 1
    call = calls[0]
    assert call['person_id'] == person['id']
    assert call['event_kind'] == 'verification'
    assert call['url'] == '/verify'
    assert call['email_html_factory'] is not None


def test_basics_only_says_what_happened_and_what_to_do(monkeypatch, make_person):
    """The copy the owner ruled on (2026-09-23). It names the result, it
    gives two things the member can actually do, and it does not accuse."""
    person = make_person()
    job = _queue_job(person['id'])

    call = _run(monkeypatch, job, _matched([]))[0]

    assert call['title'] == 'We could not match your selfie to your photos'
    assert call['body'] == (
        'Your photos need to clearly show your face in good light. '
        'Update a photo, or try the check again.')
    assert call['email_subject'] == (
        'We could not match your selfie to your photos')


def test_basics_only_never_reads_as_an_accusation(monkeypatch, make_person):
    person = make_person()
    job = _queue_job(person['id'])

    call = _run(monkeypatch, job, _matched([]))[0]
    words = f"{call['title']} {call['body']}".lower()

    for accusation in ('fake', 'fraud', 'reject', 'denied', 'violat',
                       'suspicious', 'someone else'):
        assert accusation not in words, f'{accusation!r} accuses the member'


def test_failure_queues_exactly_one_notification(monkeypatch, make_person):
    person = make_person()
    job = _queue_job(person['id'])

    calls = _run(monkeypatch, job, _rejected())

    assert len(calls) == 1
    call = calls[0]
    assert call['person_id'] == person['id']
    assert call['event_kind'] == 'verification'
    assert call['url'] == '/verify'
    assert call['email_html_factory'] is not None


def test_failure_says_it_did_not_pass_and_offers_a_retry(monkeypatch, make_person):
    person = make_person()
    job = _queue_job(person['id'])

    call = _run(monkeypatch, job, _rejected())[0]

    assert call['title'] == 'Your verification check did not pass'
    assert call['body'] == 'You can try the check again when you are ready.'
    assert call['email_subject'] == 'Your verification check did not pass'


def test_failure_never_asserts_a_reason_we_cannot_evidence(monkeypatch, make_person):
    """The classifier returns a truthiness score, not an explanation. The
    member is told the outcome and nothing we would have to defend."""
    person = make_person()
    job = _queue_job(person['id'])

    call = _run(monkeypatch, job, _rejected())[0]
    words = f"{call['title']} {call['body']}".lower()

    assert REJECTION_REASON.lower() not in words
    assert 'because' not in words
    for accusation in ('fake', 'fraud', 'edited', 'screenshot', 'smiling',
                       'lying', 'someone else'):
        assert accusation not in words, f'{accusation!r} asserts a reason'


# ---------------------------------------------------------------------------
# The preference still gates it
# ---------------------------------------------------------------------------

def _delivery_harness(monkeypatch) -> list[str]:
    """Run the real notify(), with a live push subscription and both
    delivery helpers replaced by recorders. Threads run inline so the
    assertion cannot race the send."""
    delivered: list[str] = []

    class _InlineThread:
        def __init__(self, target=None, kwargs=None, **_ignored):
            self._target = target
            self._kwargs = kwargs or {}

        def start(self):
            self._target(**self._kwargs)

    monkeypatch.setattr(
        notifications, 'threading', SimpleNamespace(Thread=_InlineThread))
    monkeypatch.setattr(notifications, 'PUSH_ENABLED', True)
    monkeypatch.setattr(
        notifications, '_send_to_user_blocking',
        lambda **kw: delivered.append('push'))
    monkeypatch.setattr(
        notifications, '_send_event_email_blocking',
        lambda **kw: delivered.append('email'))
    return delivered


def _set_preference(person_id: int, **columns) -> None:
    cols = ', '.join(columns)
    values = ', '.join(f'%({c})s' for c in columns)
    updates = ', '.join(f'{c} = EXCLUDED.{c}' for c in columns)
    with api_tx() as tx:
        tx.execute(
            f'INSERT INTO notification_preference (person_id, {cols}) '
            f'VALUES (%(person_id)s, {values}) '
            f'ON CONFLICT (person_id) DO UPDATE SET {updates}',
            dict(person_id=person_id, **columns),
        )


def _subscribe(person_id: int) -> None:
    with api_tx() as tx:
        tx.execute(
            'INSERT INTO push_subscription (person_id, endpoint, p256dh, auth) '
            "VALUES (%(id)s, %(endpoint)s, 'k', 'a')",
            dict(id=person_id, endpoint=f'https://push.invalid/{uuid4()}'),
        )


@pytest.mark.parametrize('result_name', ['basics_only', 'failure'])
def test_a_member_with_verification_notifications_on_is_told(
        monkeypatch, make_person, result_name):
    """The control for the test below: this harness does deliver."""
    person = make_person()
    _subscribe(person['id'])
    _set_preference(person['id'], push_verification=True)
    delivered = _delivery_harness(monkeypatch)
    job = _queue_job(person['id'])

    async def fake_verify(**_kwargs):
        return _matched([]) if result_name == 'basics_only' else _rejected()

    monkeypatch.setattr(runner, 'verify', fake_verify)
    asyncio.run(runner.do_verification_job(job))

    assert delivered == ['push']


@pytest.mark.parametrize('result_name', ['basics_only', 'failure'])
def test_a_member_who_turned_verification_notifications_off_gets_none(
        monkeypatch, make_person, result_name):
    """push_verification is the same switch the granted tiers obey. With it
    off, and the email channel off too, the member hears nothing."""
    person = make_person()
    _subscribe(person['id'])
    _set_preference(
        person['id'], push_verification=False, email_verification=False)
    delivered = _delivery_harness(monkeypatch)
    job = _queue_job(person['id'])

    async def fake_verify(**_kwargs):
        return _matched([]) if result_name == 'basics_only' else _rejected()

    monkeypatch.setattr(runner, 'verify', fake_verify)
    asyncio.run(runner.do_verification_job(job))

    assert delivered == []


# ---------------------------------------------------------------------------
# A notifier that blows up never costs the member their result
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'result_name, expected_status, expected_level',
    [('basics_only', 'success', 'Basics only'),
     ('failure', 'failure', 'No verification'),
     ('bronze', 'success', 'Photos')])
def test_a_raising_notifier_still_lets_the_job_finish(
        monkeypatch, make_person, result_name, expected_status, expected_level):
    person = make_person()
    job = _queue_job(person['id'])

    def boom(*_a, **_kw):
        raise RuntimeError('notifier down')

    result = {
        'basics_only': _matched([]),
        'failure': _rejected(),
        'bronze': _matched(['photo-uuid']),
    }[result_name]

    _run(monkeypatch, job, result, notify=boom)

    assert _job_row(job.id)['status'] == expected_status
    # A failed run passes 'No verification' and the SQL discards it, so the
    # person keeps the level they already had, which for a new member is the
    # 'No verification' default (person.verification_level_id DEFAULT 1).
    assert _level_name(person['id']) == expected_level


# ---------------------------------------------------------------------------
# The granted tiers are untouched
# ---------------------------------------------------------------------------

def test_bronze_still_notifies_exactly_as_before(monkeypatch, make_person):
    person = make_person()
    job = _queue_job(person['id'], claimed_uuids=['photo-1'])

    calls = _run(monkeypatch, job, _matched(['photo-1']))

    assert len(calls) == 1
    call = calls[0]
    assert call['event_kind'] == 'verification'
    assert call['title'] == "You're verified"
    assert call['body'] == 'Your Bronze verification was approved.'
    assert call['url'] == '/verify'
    assert call['email_subject'] == "You're verified on Ahavah"
    assert _level_name(person['id']) == 'Photos'


def test_silver_still_notifies_exactly_as_before(monkeypatch, make_person):
    person = make_person()
    job = _queue_job(
        person['id'], claimed_uuids=['photo-1'],
        silver_burst_uuids=['burst-1', 'burst-2', 'burst-3'])

    calls = _run(
        monkeypatch, job,
        _matched(['photo-1', 'burst-1', 'burst-2', 'burst-3']))

    assert len(calls) == 1
    call = calls[0]
    assert call['title'] == "You're verified"
    assert call['body'] == 'Your Silver verification was approved.'
    assert call['email_subject'] == "You're verified on Ahavah"


def test_the_tier_email_is_still_the_verified_template(monkeypatch, make_person):
    """The approved path's email is not touched by this task."""
    person = make_person()
    job = _queue_job(person['id'], claimed_uuids=['photo-1'])

    call = _run(monkeypatch, job, _matched(['photo-1']))[0]
    html = call['email_html_factory']('https://example.invalid/unsub')

    assert 'title-verified.png' in html
    assert 'Bronze verification was approved' in html


# ---------------------------------------------------------------------------
# The two new emails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('builder_name', [
    'verification_basics_only_email', 'verification_not_passed_email'])
def test_the_new_emails_ship_without_a_hand_rolled_headline(builder_name):
    """No brand title image says a check did not pass, and display type is
    never hand rolled, so these two go out with no headline at all."""
    import emails.notification as notification_emails

    html = getattr(notification_emails, builder_name)(
        'https://example.invalid/unsub')

    assert '<h1' not in html.lower()
    # title_image() emits this img pair. Neither email carries one. (The
    # shell's <style> block names the classes either way, so the tag is what
    # has to be absent.)
    assert '<img class="e-title-light"' not in html
    assert '<img class="e-title-dark"' not in html
    assert 'https://example.invalid/unsub' in html


def test_the_basics_only_email_carries_the_approved_copy():
    from emails.notification import verification_basics_only_email

    html = verification_basics_only_email('https://example.invalid/unsub')

    assert 'We could not match your selfie to your photos' in html
    assert 'good light' in html


def test_the_not_passed_email_names_no_reason():
    from emails.notification import verification_not_passed_email

    html = verification_not_passed_email('https://example.invalid/unsub')

    assert 'did not pass' in html
    assert 'try the check again' in html
    # 'because' is not checked here: the shared footer says "You're getting
    # this because of your Ahavah notification settings", which is about the
    # mail and not about the verification outcome.
    for accusation in ('fake', 'fraud', 'edited', 'screenshot', 'smiling'):
        assert accusation not in html.lower()
