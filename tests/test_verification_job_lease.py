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
import threading
import time
from uuid import uuid4

import psycopg
import pytest

import database
import service.cron.verificationjobrunner as runner
from database import api_tx
from service.cron.verificationjobrunner.sql import (
    Q_ABANDONED_VERIFICATION_JOBS,
    Q_CLAIM_VERIFICATION_JOB,
    Q_UPDATE_VERIFICATION_STATUS,
)
from service.verificationlease import (
    VERIFICATION_LEASE_SECONDS,
    VERIFICATION_MAX_JOBS_PER_TICK,
    VERIFICATION_MAX_REAPS,
)
from verification import Failure, Success, VerificationResult


_LEASE_PARAMS = dict(
    lease_seconds=VERIFICATION_LEASE_SECONDS,
    max_reaps=VERIFICATION_MAX_REAPS,
)


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
            dict(**_LEASE_PARAMS, max_jobs=VERIFICATION_MAX_JOBS_PER_TICK),
        ).fetchall()
    return [r['id'] for r in rows]


def _abandoned_ids(limit: int = 500) -> list[int]:
    """The ids the tick would end rather than run again."""
    with api_tx() as tx:
        rows = tx.execute(
            Q_ABANDONED_VERIFICATION_JOBS,
            dict(**_LEASE_PARAMS, max_jobs=limit),
        ).fetchall()
    return [r['id'] for r in rows]


def _counts() -> dict:
    from service.admin.queries import Q_VERIFICATION_JOBS

    with api_tx() as tx:
        return dict(tx.execute(Q_VERIFICATION_JOBS, _LEASE_PARAMS).fetchone())


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


def _silence_notifications(monkeypatch, notified: list | None = None):
    """Record notifications instead of sending them. `notified` collects one
    entry per notification, so a test can prove the member was told once,
    or not at all."""
    import service.notifications as notifications

    def record(person_id, event_kind, **kw):
        if notified is not None:
            notified.append(dict(person_id=person_id, event_kind=event_kind, **kw))

    monkeypatch.setattr(notifications, 'notify', record)
    monkeypatch.setattr(notifications, 'send_to_user_safe', lambda *a, **kw: None)


def _run(monkeypatch, job, result, notified: list | None = None,
         during_verify=None) -> list[dict]:
    """Run one job with a stubbed classifier and a silent notifier.
    Returns one entry per classifier call, so a test can prove the
    classifier was NOT called for a job the worker failed to claim.

    `during_verify` runs while this worker's classifier call is notionally
    in flight, which is where another worker gets the chance to reap the
    row out from under it."""
    calls: list[dict] = []

    async def fake_verify(**kwargs):
        calls.append(kwargs)
        if during_verify is not None:
            during_verify()
        return result

    monkeypatch.setattr(runner, 'verify', fake_verify)
    _silence_notifications(monkeypatch, notified)

    asyncio.run(runner.do_verification_job(job))
    return calls


def _open_worker_connection():
    """A second worker's own connection, for the races that need two.

    `api_tx` shares one process-wide connection behind a lock, so a test
    that needs two transactions open at once cannot use it for both.
    """
    return psycopg.connect(database._api_conninfo,
                           row_factory=psycopg.rows.dict_row)


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
    person = make_person()
    _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30,
        reap_count=VERIFICATION_MAX_REAPS)

    counts = _counts()

    assert counts['verification_stuck'] >= 1
    assert counts['verification_abandoned'] >= 1


def test_a_failed_job_is_counted_for_the_operator(make_person):
    """The operator asked for stuck AND failed. A classifier that starts
    rejecting everything reads as a wall of failures, not as silence."""
    person = make_person()
    _insert_job(person['id'], status='failure')

    assert _counts()['verification_failed'] >= 1


def test_a_running_row_with_no_lease_is_counted_as_stuck(make_person):
    """A row with no lease is one the picker refuses to reap, so counting it
    as healthily running would make it invisible to the reaper and to the
    operator at the same time, which is the worst of the two readings.
    Migration 0053 and the claim query between them should make this
    unreachable; if it is ever reached it has to be visible."""
    person = make_person()
    job_id = _insert_job(person['id'], status='running', running_age_seconds=None)

    before = _counts()
    assert job_id not in _eligible_ids(), 'the picker still refuses this row'

    # A second identical row moves `stuck`, not `running`.
    _insert_job(person['id'], status='running', running_age_seconds=None)
    after = _counts()

    assert after['verification_stuck'] == before['verification_stuck'] + 1
    assert after['verification_running'] == before['verification_running']


def test_the_picker_hands_a_worker_a_bounded_batch(make_person):
    """An unbounded listing after an OpenAI outage puts the worker inside
    one `for` loop for as long as the backlog is deep, at up to 136.5
    seconds a job, never returning to the top of verify_forever and never
    re-reading the queue."""
    person = make_person()
    for _ in range(VERIFICATION_MAX_JOBS_PER_TICK + 2):
        _insert_job(person['id'], status='queued')

    assert len(_eligible_ids()) == VERIFICATION_MAX_JOBS_PER_TICK


# ---------------------------------------------------------------------------
# An abandoned job ends, and says so
# ---------------------------------------------------------------------------

def _end_abandoned(monkeypatch, job_id: int, person_id: int,
                   notified: list | None = None) -> None:
    _silence_notifications(monkeypatch, notified)
    asyncio.run(runner.end_abandoned_verification_job(job_id, person_id))


def _abandoned_job(person_id: int) -> int:
    return _insert_job(
        person_id, status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30,
        reap_count=VERIFICATION_MAX_REAPS)


def test_an_abandoned_job_is_listed_for_the_tick_to_end(make_person):
    person = make_person()
    job_id = _abandoned_job(person['id'])

    assert job_id not in _eligible_ids(), 'it must not be run again'
    assert job_id in _abandoned_ids(), 'but something has to finish it'


def test_an_abandoned_job_reaches_a_terminal_state(monkeypatch, make_person):
    """The defect: the picker excluded it, nothing else wrote the row, and
    it sat in 'running' until garbagerecords deleted it three days later.
    /check-verification said `pending` the whole time."""
    person = make_person()
    job_id = _abandoned_job(person['id'])

    _end_abandoned(monkeypatch, job_id, person['id'])

    row = _job_row(job_id)
    assert row['status'] == 'failure'
    assert row['running_since'] is None, 'a finished row holds no lease'
    assert row['level_name'] == 'No verification'


def test_an_abandoned_job_tells_the_member_once(monkeypatch, make_person):
    """The wave's constraint is that a member is never left waiting with no
    exit. The web client's 90 second poll timeout is not an exit: it is a
    message that does not know the check is dead."""
    person = make_person()
    job_id = _abandoned_job(person['id'])

    notified: list[dict] = []
    _end_abandoned(monkeypatch, job_id, person['id'], notified)

    assert len(notified) == 1
    call = notified[0]
    assert call['person_id'] == person['id']
    assert call['event_kind'] == 'verification'
    assert call['title'] == 'Your verification check did not pass'
    assert call['url'] == '/verify'

    # A second worker that listed the same row writes nothing and says
    # nothing. One dead check, one message.
    again: list[dict] = []
    _end_abandoned(monkeypatch, job_id, person['id'], again)
    assert again == []


def test_an_abandoned_job_names_no_reason(monkeypatch, make_person):
    """Nothing came back from the classifier at all, so there is nothing to
    report beyond that the check did not finish. The message is rendered on
    the rejected card."""
    person = make_person()
    job_id = _abandoned_job(person['id'])

    _end_abandoned(monkeypatch, job_id, person['id'])

    with api_tx() as tx:
        message = tx.execute(
            'SELECT message FROM verification_job WHERE id = %(id)s',
            dict(id=job_id),
        ).fetchone()['message']

    assert message == 'This check did not finish. You can try again.'
    for accusation in ('edited', 'screenshot', 'fake', 'fraud', 'reject',
                       'denied', 'suspicious', 'someone else'):
        assert accusation not in message.lower()


def test_ending_an_abandoned_job_never_lowers_the_tier(monkeypatch, make_person):
    """Ruling 2 holds here as much as it does for a reap. An attempt that
    never produced an answer takes nothing away."""
    person = make_person()
    _grant(person['id'], 'Photos', 'silver')
    job_id = _abandoned_job(person['id'])

    _end_abandoned(monkeypatch, job_id, person['id'])

    after = _person_row(person['id'])
    assert after['tier'] == 'silver'
    assert after['level_name'] == 'Photos'


def test_an_abandoned_job_stops_reading_as_pending(monkeypatch, make_person):
    """The member's actual exit. Before this the endpoint reported `pending`
    for three days, which is the API telling them to keep waiting for an
    answer that was never coming."""
    from service.person import get_check_verification
    from types import SimpleNamespace

    person = make_person()
    job_id = _abandoned_job(person['id'])

    s = SimpleNamespace(person_id=person['id'], person_uuid=person['uuid'])
    assert get_check_verification(s=s)['outcome'] == 'pending'

    _end_abandoned(monkeypatch, job_id, person['id'])

    assert get_check_verification(s=s)['outcome'] == 'none'


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
    """Two workers list the same stale row and race on the claim, for real:
    one transaction is held open on its own connection while the runner's
    claim queues behind its row lock.

    The old version of this test ran the two claims one after the other and
    let the first one finish the job, so the second claim failed the
    `status = 'queued' OR 'running'` test and never reached the lease at
    all. It passed for a reason that had nothing to do with the thing it is
    named after.

    Two properties are asserted here. The loser does not reach the
    classifier, which is the safety property: one submission, one paid
    vision call, and a member's selfie is never sent twice. And the loser
    does not RAISE, which is the property that was broken: at the
    connection's default REPEATABLE READ this claim aborts with
    SerializationFailure, and that exception escapes do_verification_job,
    kills the rest of the tick's loop and surfaces as what looks like a
    database fault.
    """
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    winner = _open_worker_connection()
    committer = None
    try:
        winner.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        won = winner.execute(
            Q_CLAIM_VERIFICATION_JOB,
            dict(verification_job_id=job_id, **_LEASE_PARAMS),
        ).fetchall()
        assert len(won) == 1, 'the first worker should have taken the row'

        # The winner commits a second from now. Until it does, its
        # transaction is open and holds the row, so the runner's claim below
        # queues behind the row lock instead of seeing anything at all.
        def commit_the_winner():
            time.sleep(1.0)
            winner.commit()

        committer = threading.Thread(target=commit_the_winner)
        committer.start()

        # The real losing worker, on the real code path.
        calls = _run(monkeypatch, _job(job_id, person['id']), _matched())
    finally:
        if committer is not None:
            committer.join(timeout=30)
        winner.close()

    assert calls == [], 'a held row must not reach the classifier'
    assert _job_row(job_id)['reap_count'] == 1


def test_the_claim_is_made_at_read_committed(monkeypatch, make_person):
    """The isolation level is not decorative, and nothing else in the repo
    would notice if it went back to the default. Both of the runner's
    transactions ask for READ COMMITTED: the claim, so a losing worker can
    re-evaluate the predicate and match nothing rather than being aborted,
    and the write, so the lease fence can report "somebody else finished
    this" by matching no row."""
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    levels: list[str] = []
    real_api_tx = runner.api_tx

    def recording_api_tx(*args, **kwargs):
        levels.append(
            args[0] if args else kwargs.get('isolation_level', 'default'))
        return real_api_tx(*args, **kwargs)

    monkeypatch.setattr(runner, 'api_tx', recording_api_tx)
    _run(monkeypatch, _job(job_id, person['id']), _matched())

    assert levels == ['read committed', 'read committed'], levels


def test_repeatable_read_is_why_the_claim_pins_its_isolation_level(make_person):
    """Why the line above has to stay. This is the defect itself, run
    against the real database on two real connections, at both levels.

    At READ COMMITTED the losing claim re-evaluates its WHERE clause
    against the winner's committed row, finds a lease refreshed a moment
    ago and returns no row, which is what the claim's comment describes and
    what do_verification_job is written to handle. At REPEATABLE READ,
    which is what every connection in this repo opens with by default,
    PostgreSQL cannot let it re-evaluate anything and aborts it instead.

    If this test ever starts failing at READ COMMITTED, the claim's whole
    concurrency story has changed and do_verification_job needs rereading.
    """
    person = make_person()
    job_id = _insert_job(
        person['id'], status='running',
        running_age_seconds=VERIFICATION_LEASE_SECONDS + 30)

    def race(isolation_level: str):
        outcome: dict = {}
        a = _open_worker_connection()
        b = _open_worker_connection()
        try:
            for conn in (a, b):
                conn.execute(
                    f'SET TRANSACTION ISOLATION LEVEL {isolation_level}')
            claim = dict(verification_job_id=job_id, **_LEASE_PARAMS)
            outcome['a'] = a.execute(
                Q_CLAIM_VERIFICATION_JOB, claim).fetchall()

            def claim_b():
                try:
                    outcome['b'] = b.execute(
                        Q_CLAIM_VERIFICATION_JOB, claim).fetchall()
                except BaseException as e:    # noqa: BLE001 - reported below
                    outcome['b_error'] = e

            t = threading.Thread(target=claim_b)
            t.start()
            time.sleep(1.0)
            a.commit()
            t.join(timeout=30)
        finally:
            for conn in (a, b):
                try:
                    conn.rollback()
                finally:
                    conn.close()
        return outcome

    committed = race('READ COMMITTED')
    assert len(committed['a']) == 1
    assert 'b_error' not in committed, committed.get('b_error')
    assert committed['b'] == [], 'the loser should have matched no row'

    # Put the row back the way the first race found it.
    with api_tx() as tx:
        tx.execute(
            """
            UPDATE verification_job
               SET running_since = NOW() - make_interval(secs => %(age)s),
                   reap_count = 0
             WHERE id = %(id)s
            """,
            dict(age=VERIFICATION_LEASE_SECONDS + 30, id=job_id),
        )

    repeatable = race('REPEATABLE READ')
    assert len(repeatable['a']) == 1
    assert isinstance(repeatable.get('b_error'),
                      psycopg.errors.SerializationFailure), (
        'REPEATABLE READ no longer aborts the losing claim. If that is a '
        'deliberate change, the isolation level pinned in '
        'do_verification_job can be revisited; until then it has to stay.')


# ---------------------------------------------------------------------------
# The lease fences the write, not just the claim
# ---------------------------------------------------------------------------

def test_a_worker_that_lost_its_lease_does_not_overwrite_the_reapers_result(
        monkeypatch, make_person):
    """Worker A claims job 7 and stalls. Its lease runs out, worker B reaps
    the row, runs it, writes `Photos` and tells the member their check
    passed. A then wakes up with an answer of its own.

    Without a fence on the write, A overwrites B's row and notifies a second
    time, so the member gets two messages about one selfie which can say
    opposite things. The claim's lease guards STARTING a run and gives
    nothing on finishing one.

    B is a real second connection, doing its reap and its write while A's
    classifier call is notionally in flight.
    """
    person = make_person()
    job_id = _insert_job(person['id'], status='queued')

    def worker_b_reaps_and_finishes():
        conn = _open_worker_connection()
        try:
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            # A's lease runs out while its call is in flight.
            conn.execute(
                """
                UPDATE verification_job
                   SET running_since = NOW() - make_interval(secs => %(age)s)
                 WHERE id = %(id)s
                """,
                dict(age=VERIFICATION_LEASE_SECONDS + 30, id=job_id),
            )
            claimed = conn.execute(
                Q_CLAIM_VERIFICATION_JOB,
                dict(verification_job_id=job_id, **_LEASE_PARAMS),
            ).fetchone()
            assert claimed, 'worker B should have been able to reap the row'
            conn.execute(
                Q_UPDATE_VERIFICATION_STATUS,
                dict(verification_job_id=job_id,
                     claimed_at=claimed['running_since'],
                     person_id=person['id'],
                     verified_uuids=['worker-b-photo'],
                     verified_age=True, verified_gender=True,
                     verified_ethnicity=False,
                     status='success', message='',
                     verification_level_name='Photos',
                     target_tier='bronze',
                     raw_json='{}'),
            )
            conn.commit()
        finally:
            conn.close()

    notified: list[dict] = []
    calls = _run(
        monkeypatch, _job(job_id, person['id']),
        _matched(verified_uuids=[]),          # A's answer: Basics only
        notified=notified,
        during_verify=worker_b_reaps_and_finishes,
    )

    assert len(calls) == 1, 'worker A did claim the row and did run'

    row = _job_row(job_id)
    assert row['level_name'] == 'Photos', (
        "worker A wrote its own answer over the reaper's")
    assert row['status'] == 'success'
    assert _person_row(person['id'])['tier'] == 'bronze'
    assert notified == [], (
        'worker A told the member a second time about one selfie')


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


# `test_a_historical_row_holds_no_level` lived here and was deleted in the
# fix wave of 2026-09-24. It inserted a row and asserted that a nullable
# column was NULL, which restates the schema rather than testing anything
# the code does. The behaviour it was reaching for, that a historical row
# reads as "unknown" and falls back to the person latch instead of being
# read as 'Basics only', is pinned by
# test_the_outcome_falls_back_to_the_person_latch_when_the_job_is_null.


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
