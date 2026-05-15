from database.asyncdatabase import api_tx
from service.cron.verificationjobrunner.sql import *
from verification import verify
from verification.messages import (
    V_SOMETHING_WENT_WRONG,
)
from service.cron.cronutil import print_stacktrace, MAX_RANDOM_START_DELAY
import asyncio
import os
import random
from dataclasses import dataclass

VERIFICATION_POLL_SECONDS = int(os.environ.get(
    'DUO_CRON_VERIFICATION_POLL_SECONDS',
    str(1), # 1 second
))

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

async def do_verification_job(verification_job: VerificationJob):
    async with api_tx() as tx:
        await tx.execute(
            Q_SET_VERIFICATION_JOB_RUNNING,
            dict(verification_job_id=verification_job.id)
        )

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

    async with api_tx() as tx:
        await tx.execute(Q_UPDATE_VERIFICATION_STATUS, params)

async def verify_once():
    async with api_tx() as tx:
        cur = await tx.execute(Q_QUEUED_VERIFICATION_JOBS)
        rows = await cur.fetchall()

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
