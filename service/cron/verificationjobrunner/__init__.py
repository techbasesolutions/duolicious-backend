from database.asyncdatabase import api_tx
from service.cron.verificationjobrunner.sql import *
from verification import verify
from verification.messages import (
    V_DID_NOT_FINISH,
    V_SOMETHING_WENT_WRONG,
)
from service.cron.cronutil import env_int, print_stacktrace, MAX_RANDOM_START_DELAY
from service.verificationlease import (
    VERIFICATION_LEASE_SECONDS,
    VERIFICATION_MAX_JOBS_PER_TICK,
    VERIFICATION_MAX_REAPS,
)
import asyncio
import random
from dataclasses import dataclass

VERIFICATION_POLL_SECONDS = env_int('DUO_CRON_VERIFICATION_POLL_SECONDS', 1) # 1 second

print(f'Hello from cron module: {__name__}')

@dataclass
class VerificationJob:
    id: int
    person_id: int
    proof_uuid: str
    claimed_uuids: list[str]
    claimed_age: int
    claimed_gender: str
    claimed_ethnicity: str | None
    # Silver tier (mig 0012): when present, the cron appends these to
    # claimed_uuids so the GPT-4.1 vision classifier confirms all 3
    # selfies show the same person AND the profile photos do too. ALL
    # entries here must come back as verified for the burst to count
    # as a Silver pass — otherwise it fails over to a Bronze run.
    silver_burst_uuids: list[str] | None = None

def _notify_verification_outcome(person_id, status, target_tier):
    """Tell the member what this attempt came to.

    Every outcome reports now. The two that used to say nothing were the
    whole defect. 'Basics only' (success, no tier) was silent on purpose, on
    the grounds that there was "no clear pass/fail to report", and a
    classifier rejection only ever pushed, so a member whose push never
    arrived heard nothing. Both left the member on a screen that never
    resolved. Gold/ID (Stripe Identity) is finalized elsewhere
    (service/identity_verification) and notified separately.

    A rejection names no reason. The classifier returns a truthiness score,
    not an explanation, so the copy reports the outcome and the retry and
    asserts nothing we could not evidence.

    Fire and forget, and never inside an api_tx: a notification that blows
    up must not cost the member the result of their check.
    """
    try:
        from service.notifications import notify
        from emails.notification import (
            new_verification_email,
            verification_basics_only_email,
            verification_not_passed_email,
        )
        if status == 'success' and target_tier:
            tier = str(target_tier).capitalize()
            notify(
                person_id, "verification",
                title="You're verified",
                body=f"Your {tier} verification was approved.",
                url="/verify",
                email_subject="You're verified on Ahavah",
                email_html_factory=lambda unsub: new_verification_email(tier, unsub),
            )
        elif status == 'success':
            notify(
                person_id, "verification",
                title="We could not match your selfie to your photos",
                body=("Your photos need to clearly show your face in good "
                      "light. Update a photo, or try the check again."),
                url="/verify",
                email_subject="We could not match your selfie to your photos",
                email_html_factory=verification_basics_only_email,
            )
        elif status == 'failure':
            notify(
                person_id, "verification",
                title="Your verification check did not pass",
                body="You can try the check again when you are ready.",
                url="/verify",
                email_subject="Your verification check did not pass",
                email_html_factory=verification_not_passed_email,
            )
    except Exception:
        import traceback
        print("verificationjobrunner notify failed:")
        print(traceback.format_exc())


async def end_abandoned_verification_job(job_id: int, person_id: int):
    """A run that has died more times than it may be retried is ended here.

    Without this the row sat in 'running' forever: the picker excludes it,
    nothing else writes it, /check-verification reported `pending` for three
    days and no notification was ever sent. The member's only exit was the
    web client's 90 second poll timeout, a message that does not know the
    check is dead. The wave's constraint is that a member is never left
    waiting with no exit, so the row goes terminal and the member is told.

    The write is fenced on the same predicate the listing used, so two
    workers that both listed the row produce one write and one
    notification.
    """
    async with api_tx('read committed') as tx:
        cur = await tx.execute(
            Q_ABANDON_VERIFICATION_JOB,
            dict(
                verification_job_id=job_id,
                message=V_DID_NOT_FINISH,
                lease_seconds=VERIFICATION_LEASE_SECONDS,
                max_reaps=VERIFICATION_MAX_REAPS,
            )
        )
        ended = await cur.fetchone()

    if not ended:
        return

    # The same notification a classifier rejection sends. The member does
    # not need to know which of the two happened, and we have nothing to
    # tell them about this one beyond that it did not pass and they can try
    # again.
    _notify_verification_outcome(person_id, 'failure', None)


async def do_verification_job(verification_job: VerificationJob):
    # Claim the row before spending a classifier call on it. The listing that
    # produced this job took no locks, so another worker may have claimed it
    # since, and a row whose lease is still fresh is being worked on right
    # now. Claiming stamps a new lease and counts the re-queue if this was a
    # reap. No claim, no run: the other worker owns the outcome.
    #
    # READ COMMITTED, deliberately, and not the connection's REPEATABLE READ
    # default. The claim is one UPDATE whose WHERE clause re-tests what the
    # listing saw, and the losing worker of a race has to be able to
    # re-evaluate that clause against the winner's committed row and match
    # nothing. Under REPEATABLE READ it cannot: PostgreSQL aborts it with
    # SerializationFailure, which escapes this function, kills the rest of
    # the tick's loop in verify_once and is swallowed by print_stacktrace as
    # what looks like a database fault. A single statement is atomic at
    # either level, so nothing is given up by narrowing it here.
    async with api_tx('read committed') as tx:
        cur = await tx.execute(
            Q_CLAIM_VERIFICATION_JOB,
            dict(
                verification_job_id=verification_job.id,
                lease_seconds=VERIFICATION_LEASE_SECONDS,
                max_reaps=VERIFICATION_MAX_REAPS,
            )
        )
        claimed = await cur.fetchone()

    if not claimed:
        return

    # The lease this worker just stamped. Q_UPDATE_VERIFICATION_STATUS
    # fences on it, so if this run overruns its lease and another worker
    # reaps the row and finishes it, this worker's result is dropped rather
    # than written over the answer the member was already given.
    claimed_at = claimed['running_since']

    is_silver = bool(verification_job.silver_burst_uuids)
    # For a Silver burst, append the additional selfies as claimed
    # photos so the classifier validates same-person across the whole
    # capture set in a single API call. The classifier's existing
    # `image_1_has_person_from_image_N` checks (up to N=8) cover this
    # natively — no prompt rewrite needed.
    classifier_claimed = list(verification_job.claimed_uuids)
    if is_silver:
        # Cap to keep total images <= 8 (classifier prompt limit).
        # Profile photos take precedence; burst frames truncate to fit.
        room = max(0, 7 - len(classifier_claimed))
        classifier_claimed = classifier_claimed + (
            list(verification_job.silver_burst_uuids or [])[:room])

    verification_result = await verify(
        proof_uuid=verification_job.proof_uuid,
        claimed_uuids=classifier_claimed,
        claimed_age=verification_job.claimed_age,
        claimed_gender=verification_job.claimed_gender,
        claimed_ethnicity=verification_job.claimed_ethnicity,
    )

    # For a Silver burst, ALL of the burst UUIDs we asked the classifier
    # about must come back as verified — otherwise the user submitted
    # 3 selfies but only some matched, which is a failure even if
    # profile photos individually did match.
    silver_passed = False
    if is_silver and verification_result.success:
        burst_in_classifier = set(
            verification_job.silver_burst_uuids[:max(0, 7 - len(verification_job.claimed_uuids))]
            if verification_job.silver_burst_uuids else []
        )
        verified_set = set(verification_result.success.verified_uuids)
        silver_passed = bool(burst_in_classifier) and burst_in_classifier.issubset(verified_set)

    if verification_result.success:
        # Tier ladder for the SQL rank-ratchet:
        #   silver = full burst (all 3 selfies) confirmed same person
        #            AND profile photos matched.
        #   bronze = at least one profile photo verified by classifier.
        #   None   = 'Basics only' (anti-spoof gestures passed but no
        #            profile photo matched the selfie). Don't bump.
        if silver_passed:
            target_tier = 'silver'
        elif verification_result.success.verified_uuids:
            target_tier = 'bronze'
        else:
            target_tier = None
        params = dict(
            verification_job_id=verification_job.id,
            claimed_at=claimed_at,
            person_id=verification_job.person_id,
            verified_uuids=verification_result.success.verified_uuids,
            verified_age=verification_result.success.is_verified_age,
            verified_gender=verification_result.success.is_verified_gender,
            verified_ethnicity=verification_result.success.is_verified_ethnicity,
            status='success',
            message='',
            verification_level_name=(
                'Photos'
                if verification_result.success.verified_uuids
                else 'Basics only'
            ),
            target_tier=target_tier,
            raw_json=verification_result.success.raw_json,
        )
    else:
        message = (
            verification_result.failure.reason
            if verification_result.failure
            else V_SOMETHING_WENT_WRONG)

        params = dict(
            verification_job_id=verification_job.id,
            claimed_at=claimed_at,
            person_id=verification_job.person_id,
            verified_uuids=[],
            verified_age=False,
            verified_gender=False,
            verified_ethnicity=False,
            status='failure',
            message=message,
            verification_level_name='No verification',
            # NULL target_tier short-circuits the rank ladder in the
            # SQL — failure runs leave ahavah_verification_tier alone.
            target_tier=None,
            raw_json=verification_result.failure.raw_json,
        )

    # READ COMMITTED for the same reason the claim is: the fence in this
    # statement reports "somebody else finished this job" by matching no
    # row, and under REPEATABLE READ a concurrent writer on the same row
    # aborts this transaction instead of letting it match none.
    async with api_tx('read committed') as tx:
        cur = await tx.execute(Q_UPDATE_VERIFICATION_STATUS, params)
        written = await cur.fetchone()

    # The fence held nothing back only if this worker still owned the row.
    # Zero here means this run overran its lease, another worker reaped the
    # job, ran it and has already told the member. Writing now would
    # overwrite that answer, and notifying now would give the member a
    # second message about one selfie that can say the opposite of the
    # first. Neither happens.
    if not written or not written['updated_jobs']:
        print(
            f'verificationjobrunner: job {verification_job.id} lost its lease '
            f'mid run; another worker has already finished it. Dropping this '
            f'result.')
        return

    # Notify the user of their verification result (gated by
    # push_verification, default on). Fire-and-forget; never block the job.
    _notify_verification_outcome(
        verification_job.person_id, params['status'], params['target_tier'])

async def verify_once():
    # Fresh submissions plus anything whose lease ran out. The listing is
    # advisory: every job re-checks its own claim before running, so a row
    # listed here and taken by another worker a moment later is simply
    # skipped.
    async with api_tx() as tx:
        cur = await tx.execute(
            Q_ELIGIBLE_VERIFICATION_JOBS,
            dict(
                lease_seconds=VERIFICATION_LEASE_SECONDS,
                max_reaps=VERIFICATION_MAX_REAPS,
                max_jobs=VERIFICATION_MAX_JOBS_PER_TICK,
            )
        )
        rows = await cur.fetchall()

    # Anything that has burned its retries and is still sitting in
    # 'running'. Nothing will ever run these again, so they are ended and
    # the member is told, rather than left reading `pending` until
    # garbagerecords deletes the row three days later.
    async with api_tx() as tx:
        cur = await tx.execute(
            Q_ABANDONED_VERIFICATION_JOBS,
            dict(
                lease_seconds=VERIFICATION_LEASE_SECONDS,
                max_reaps=VERIFICATION_MAX_REAPS,
                max_jobs=VERIFICATION_MAX_JOBS_PER_TICK,
            )
        )
        abandoned = await cur.fetchall()

    for row in abandoned:
        await end_abandoned_verification_job(row['id'], row['person_id'])

    verification_jobs = [
        VerificationJob(
            id=row['id'],
            person_id=row['person_id'],
            proof_uuid=row['proof_uuid'],
            claimed_uuids=row['claimed_uuids'],
            claimed_age=row['claimed_age'],
            claimed_gender=row['claimed_gender'],
            claimed_ethnicity=row['claimed_ethnicity'],
            silver_burst_uuids=row.get('silver_burst_uuids'),
        )
        for row in rows
    ]

    for verification_job in verification_jobs:
        await do_verification_job(verification_job)

async def verify_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(verify_once)
        await asyncio.sleep(VERIFICATION_POLL_SECONDS)
