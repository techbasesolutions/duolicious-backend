"""A verification job that dies mid run is retried, and is visible (task 4).

The runner set a row to 'running' before calling the classifier and the
picker only ever selected 'queued', so a worker that died between the two
left the row in 'running' forever. Nothing retried it, nothing surfaced it,
and garbagerecords deleted it three days later. The member sat on a polling
screen and never got an answer.

These tests pin the lease: a run that has blown it is claimable again, a run
that has not is not, a row that has burned its retries stops being claimed
and starts being counted, and a claim another worker already won cannot be
won twice.

They also pin the two rulings of 2026-09-23.

  1. The outcome is persisted on the job row (`verification_level_id`), so a
     per-job answer is never read off the per-person latch. Historical rows
     hold NULL and fall back to the latch.

  2. The reaper never revokes. Re-queueing touches the job row and nothing
     else: `person.verification_level_id` and `person.ahavah_verification_tier`
     come out of a reap exactly as they went in. A member who earned a tier
     keeps it.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import service.cron.verificationjobrunner as runner
from database import api_tx
from service.verificationlease import (
    VERIFICATION_LEASE_SECONDS,
    VERIFICATION_MAX_REAPS,
)
from verification import Failure, Success, VerificationResult


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _insert_job(person_id: int, *, status: str = 'queued',
                running_age_seconds: int | None = None,
                reap_count: int = 0) -> int:
    """A verification_job row in a chosen state.

    `running_age_seconds` is how long ago the run started; None leaves
    `running_since` NULL, which is what every row written before migration
    0053 holds.
    """
    with api_tx() as tx:
        return tx.execute(
            """
            INSERT INTO verification_job (
                person_id, status, message, photo_uuid,
                running_since, reap_count)
            VALUES (
                %(person_id)s, %(status)s, '', %(proof)s,
                CASE
                    WHEN %(age)s::INT IS NULL THEN NULL
                    ELSE NOW() - make_interval(secs => %(age)s)
                END,
                %(reap_count)s)
            RETURNING id
            """,
            dict(person_id=person_id, status=status, proof=f'proof-{uuid4()}',
                 age=running_age_seconds, reap_count=reap_count),
        ).fetchone()['id']


def _job_row(job_id: int) -> dict:
    with api_tx() as tx:
        return dict(tx.execute(
            """
            SELECT vj.status, vj.reap_count, vj.running_since,
                   vj.verification_level_id, vl.name AS level_name
              FROM verification_job AS vj
              LEFT JOIN verification_level AS vl
                     ON vl.id = vj.verification_level_id
             WHERE vj.id = %(id)s
            """,
            dict(id=job_id),
        ).fetchone())


def _person_row(person_id: int) -> dict:
    with api_tx() as tx:
        return dict(tx.execute(
            """
            SELECT person.ahavah_verification_tier AS tier,
                   person.verification_level_id AS level_id,
                   vl.name AS level_name
              FROM person
              LEFT JOIN verification_level AS vl
                     ON vl.id = person.verification_level_id
             WHERE person.id = %(id)s
            """,
            dict(id=person_id),
        ).fetchone())


def _grant(person_id: int, level_name: str, tier: str) -> None:
    """Put a member where a first, successful attempt would have left them."""
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE person
               SET verification_level_id = (
                       SELECT id FROM verification_level
                        WHERE name = %(name)s),
                   ahavah_verification_tier = %(tier)s
             WHERE id = %(id)s
            """,
            dict(name=level_name, tier=tier, id=person_id),
        )


def _eligible_ids() -> list[int]:
    """The ids the picker would hand a worker on this tick."""
    from service.cron.verificationjobrunner.sql import Q_ELIGIBLE_VERIFICATION_JOBS

    with api_tx() as tx:
        rows = tx.execute(
            Q_ELIGIBLE_VERIFICATION_JOBS,
            dict(lease_seconds=VERIFICATION_LEASE_SECONDS,
                 max_reaps=VERIFICATION_MAX_REAPS),
        ).fetchall()
    return [r['id'] for r in rows]


def _job(job_id: int, person_id: int, *, claimed_uuids=None,
         silver_burst_uuids=None) -> runner.VerificationJob:
    return runner.VerificationJob(
        id=job_id,
        person_id=person_id,
        proof_uuid=f'proof-{uuid4()}',
        claimed_uuids=list(claimed_uuids or []),
        claimed_age=30,
        claimed_gender='Man',
        claimed_ethnicity=None,
        silver_burst_uuids=silver_burst_uuids,
    )


def _matched(verified_uuids=()) -> VerificationResult:
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


def _rejected() -> VerificationResult:
    return VerificationResult(
        success=None,
        failure=Failure(reason='Our AI could not read it.', raw_json='{}'),
    )


def _run(monkeypatch, job, result) -> list[dict]:
    """Run one job with a stubbed classifier and a silent notifier.
    Returns one entry per classifier call, so a test can prove the
    classifier was NOT called for a job the worker failed to claim."""
    import service.notifications as notifications

    calls: list[dict] = []

    async def fake_verify(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(runner, 'verify', fake_verify)
    monkeypatch.setattr(notifications, 'notify', lambda *a, **kw: None)
    monkeypatch.setattr(notifications, 'send_to_user_safe', lambda *a, **kw: None)

    asyncio.run(runner.do_verification_job(job))
    return calls


# ---------------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------------

def test_a_stale_running_row_is_picked_up_again(make_person):
    """The whole defect. A worker died after setting 'running' and before
    writing a result, so this row would have sat here until garbagerecords
    removed it."""
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    assert job_id in _eligible_ids()


def test_a_fresh_running_row_is_not_picked_up(make_person):
    """The risk the lease introduces is reaping a job that is merely slow:
    the classifier call is retried by the OpenAI SDK and can legitimately
    take over two minutes. A run inside its lease is off limits."""
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS - 30)

    assert job_id not in _eligible_ids()


def test_a_queued_row_is_still_picked_up(make_person):
    """The normal path is unchanged."""
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    assert job_id in _eligible_ids()


def test_a_running_row_with_no_running_since_is_left_alone(make_person):
    """Migration 0053 stamps every row that was already 'running', so a NULL
    here after the deploy means nothing claimed this row through the claim
    query. Reaping on 'unknown age' would be reaping on a guess."""
    person = make_person()
    job_id = _insert_job(person['id'], status='running', running_age_seconds=None)

    assert job_id not in _eligible_ids()


def test_a_row_past_the_retry_ceiling_is_not_picked_up(make_person):
    """Three attempts at the same selfie that all die is not bad luck. It
    stops being retried rather than burning a classifier call a lease."""
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30,
        reap_count=VERIFICATION_MAX_REAPS)

    assert job_id not in _eligible_ids()


def test_a_row_past_the_retry_ceiling_is_counted_as_stuck(make_person):
    """Not picked up must not mean invisible. That is the same silence this
    wave exists to remove, moved from the member to the operator."""
    from service.admin.queries import Q_VERIFICATION_JOBS

    person = make_person()
    _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30,
        reap_count=VERIFICATION_MAX_REAPS)

    with api_tx() as tx:
        counts = dict(tx.execute(
            Q_VERIFICATION_JOBS,
            dict(lease_seconds=VERIFICATION_LEASE_SECONDS,
                 max_reaps=VERIFICATION_MAX_REAPS),
        ).fetchone())

    assert counts['verification_stuck'] >= 1
    assert counts['verification_abandoned'] >= 1


def test_a_failed_job_is_counted_for_the_operator(make_person):
    """The operator asked for stuck AND failed. A classifier that starts
    rejecting everything reads as a wall of failures, not as silence."""
    from service.admin.queries import Q_VERIFICATION_JOBS

    person = make_person()
    _insert_job(person['id'], status='failure')

    with api_tx() as tx:
        counts = dict(tx.execute(
            Q_VERIFICATION_JOBS,
            dict(lease_seconds=VERIFICATION_LEASE_SECONDS,
                 max_reaps=VERIFICATION_MAX_REAPS),
        ).fetchone())

    assert counts['verification_failed'] >= 1


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------

def test_claiming_a_stale_row_counts_the_reap(monkeypatch, make_person):
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    calls = _run(monkeypatch, _job(job_id, person['id']), _matched())

    assert len(calls) == 1, 'the reaped job should have been re-run'
    assert _job_row(job_id)['reap_count'] == 1


def test_claiming_a_queued_row_does_not_count_a_reap(monkeypatch, make_person):
    """A first run is not a retry."""
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    _run(monkeypatch, _job(job_id, person['id']), _matched())

    assert _job_row(job_id)['reap_count'] == 0


def test_the_reaper_cannot_take_a_row_another_worker_holds(monkeypatch, make_person):
    """Both workers list the row while it is stale. The first claim wins and
    refreshes the lease; the second re-reads the row and finds it fresh, so
    the selfie is not sent to the classifier twice on one submission."""
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    first = _run(monkeypatch, _job(job_id, person['id']), _matched())
    assert len(first) == 1
    assert _job_row(job_id)['reap_count'] == 1

    # Worker two, acting on the listing it took before worker one claimed.
    second = _run(monkeypatch, _job(job_id, person['id']), _matched())

    assert second == [], 'a held row must not reach the classifier'
    assert _job_row(job_id)['reap_count'] == 1


def test_a_row_past_the_ceiling_is_not_claimable(monkeypatch, make_person):
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30,
        reap_count=VERIFICATION_MAX_REAPS)

    calls = _run(monkeypatch, _job(job_id, person['id']), _matched())

    assert calls == []
    assert _job_row(job_id)['reap_count'] == VERIFICATION_MAX_REAPS


def test_a_finished_job_releases_its_lease(monkeypatch, make_person):
    """`running_since` is the lease. A finished row keeps none, so it can
    never be read as a run that blew its deadline."""
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    _run(monkeypatch, _job(job_id, person['id']), _matched())

    row = _job_row(job_id)
    assert row['status'] == 'success'
    assert row['running_since'] is None


# ---------------------------------------------------------------------------
# Ruling 2: the reaper never revokes
# ---------------------------------------------------------------------------

def test_requeueing_leaves_the_earned_tier_untouched(monkeypatch, make_person):
    """A member passed, earned Bronze, tried again, and that attempt died.
    Re-queueing the dead attempt may not cost them the tier the first one
    earned. A retry can only add."""
    person = make_person()
    _grant(person['id'], 'Photos', 'bronze')
    before = _person_row(person['id'])

    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    from service.cron.verificationjobrunner.sql import Q_CLAIM_VERIFICATION_JOB
    with api_tx() as tx:
        tx.execute(
            Q_CLAIM_VERIFICATION_JOB,
            dict(verification_job_id=job_id,
                 lease_seconds=VERIFICATION_LEASE_SECONDS,
                 max_reaps=VERIFICATION_MAX_REAPS),
        )

    after = _person_row(person['id'])
    assert after['tier'] == before['tier'] == 'bronze'
    assert after['level_id'] == before['level_id']
    assert after['level_name'] == 'Photos'


def test_a_reaped_run_that_matches_nothing_never_lowers_the_tier(
        monkeypatch, make_person):
    """The whole reap path, end to end, on the member most at risk: one who
    already holds a tier and whose retry matches no photo."""
    person = make_person()
    _grant(person['id'], 'Photos', 'silver')

    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    _run(monkeypatch, _job(job_id, person['id']), _matched(verified_uuids=[]))

    assert _person_row(person['id'])['tier'] == 'silver'


def test_a_reaped_run_that_is_rejected_never_lowers_the_tier(
        monkeypatch, make_person):
    person = make_person()
    _grant(person['id'], 'Photos', 'bronze')

    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    _run(monkeypatch, _job(job_id, person['id']), _rejected())

    after = _person_row(person['id'])
    assert after['tier'] == 'bronze'
    assert after['level_name'] == 'Photos', \
        'a failure branch must not rewrite the person latch'


# ---------------------------------------------------------------------------
# Ruling 1: the outcome is persisted on the job
# ---------------------------------------------------------------------------

def test_the_level_is_written_on_the_job_row_on_success(monkeypatch, make_person):
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    _run(monkeypatch, _job(job_id, person['id']), _matched(verified_uuids=['u1']))

    assert _job_row(job_id)['level_name'] == 'Photos'


def test_the_level_is_written_on_the_job_row_for_basics_only(
        monkeypatch, make_person):
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    _run(monkeypatch, _job(job_id, person['id']), _matched(verified_uuids=[]))

    assert _job_row(job_id)['level_name'] == 'Basics only'


def test_the_level_is_written_on_the_job_row_on_failure(monkeypatch, make_person):
    """The person latch is only written on success, which is why the job row
    is the only place a failed attempt's own answer can live."""
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    _run(monkeypatch, _job(job_id, person['id']), _rejected())

    assert _job_row(job_id)['level_name'] == 'No verification'


def test_a_historical_row_holds_no_level(make_person):
    """Backfilled as NULL by design. Nothing invents an answer for an
    attempt that finished before the column existed."""
    person = make_person()
    job_id = _insert_job(person['id'], status='success')

    assert _job_row(job_id)['verification_level_id'] is None


def test_the_outcome_is_read_from_the_job_not_the_person(make_person):
    """Two attempts can be in flight at once: nothing constrains
    verification_job to one row per person and both upload routes
    delete-then-insert. The person latch is written by whichever attempt
    finished LAST, while the endpoint reports the NEWEST attempt, so reading
    the level off the person can describe a different run than the status
    does. Here the newest job matched nothing and the latch says Photos."""
    from service.person import get_check_verification
    from types import SimpleNamespace

    person = make_person()
    _grant(person['id'], 'Photos', 'bronze')

    job_id = _insert_job(person['id'], status='success')
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE verification_job
               SET verification_level_id = (
                       SELECT id FROM verification_level
                        WHERE name = 'Basics only')
             WHERE id = %(id)s
            """,
            dict(id=job_id),
        )

    row = get_check_verification(
        s=SimpleNamespace(person_id=person['id'], person_uuid=person['uuid']))

    assert row['verification_level_name'] == 'Basics only'
    assert row['outcome'] == 'basics_only'
    assert row['ahavah_verification_tier'] == 'bronze', \
        'the tier the first attempt earned is still reported'


def test_the_outcome_falls_back_to_the_person_latch_when_the_job_is_null(
        make_person):
    """Historical rows. NULL on the job means unknown, not 'Basics only'."""
    from service.person import get_check_verification
    from types import SimpleNamespace

    person = make_person()
    _grant(person['id'], 'Photos', 'bronze')
    _insert_job(person['id'], status='success')

    row = get_check_verification(
        s=SimpleNamespace(person_id=person['id'], person_uuid=person['uuid']))

    assert row['verification_level_name'] == 'Photos'
    assert row['outcome'] == 'photos'


def test_the_lease_still_covers_what_the_sdk_can_actually_take():
    """VERIFICATION_LEASE_SECONDS is derived, not chosen, and the derivation
    in service/verificationlease.py rests on one number this repo does not
    own: the OpenAI SDK's default `max_retries`.

    One verify() call is bounded at (max_retries + 1) attempts of 45 seconds
    each, plus the SDK's own backoff. If a dependency bump raises that
    default, the real ceiling moves past the lease and the reaper starts
    re-queueing jobs that are merely being retried: the member's selfie goes
    to the classifier twice and we pay for it twice. Nothing else in this
    repo would notice, which is why the assumption is pinned here rather
    than left in a comment.

    If this fails after an upgrade, redo the arithmetic in
    service/verificationlease.py and move the lease. Do not delete the test.
    """
    from openai import AsyncOpenAI
    from service.verificationlease import VERIFICATION_LEASE_SECONDS
    from verification import VERIFICATION_HTTP_TIMEOUT_SECONDS

    attempts = AsyncOpenAI(api_key='test-key-not-used').max_retries + 1
    # 0.5 then 1.0 second, per INITIAL_RETRY_DELAY doubling, capped at 8.0.
    backoff = sum(min(0.5 * 2 ** n, 8.0) for n in range(attempts - 1))
    worst_case = attempts * VERIFICATION_HTTP_TIMEOUT_SECONDS + backoff

    assert VERIFICATION_LEASE_SECONDS > worst_case, (
        f'the lease is {VERIFICATION_LEASE_SECONDS}s but one verify() call '
        f'can now take up to {worst_case}s ({attempts} attempts). A reaper '
        f'firing inside that window double charges a vision call and sends '
        f"the member's selfie to the classifier twice.")
