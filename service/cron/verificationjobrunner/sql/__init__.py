# Everything this tick may work on: a fresh submission, or a run that blew
# its lease and is therefore dead rather than slow.
#
# A row is only reapable when it has a lease to blow. `running_since IS NOT
# NULL` is load bearing: migration 0053 stamps every row that was already
# 'running', so a NULL after that deploy means nothing ever claimed this row
# through Q_CLAIM_VERIFICATION_JOB, and reaping on an unknown age would be
# reaping on a guess.
#
# This listing takes no locks. It cannot: the worker runs the jobs one at a
# time and a lock held across a 45 second classifier call would be a lock
# held across a third party outage. Two workers can therefore list the same
# row, which is exactly what Q_CLAIM_VERIFICATION_JOB arbitrates.
Q_ELIGIBLE_VERIFICATION_JOBS = """
SELECT
    id,
    person_id,
    photo_uuid AS proof_uuid,
    -- Silver tier (mig 0012): when populated, the cron appends these
    -- to claimed_uuids and requires all of them to come back as
    -- 'same person' from the classifier; on success the user is
    -- promoted to 'silver' instead of 'bronze'. NULL for Bronze runs.
    silver_burst_uuids,
    ARRAY(
        SELECT
            uuid
        FROM
            photo
        WHERE
            photo.person_id = vj.person_id
        ORDER BY
            position
    ) AS claimed_uuids,
    (
        SELECT
            EXTRACT(YEAR FROM AGE(date_of_birth)) AS age
        FROM
            person
        WHERE
            person.id = vj.person_id
    ) AS claimed_age,
    (
        SELECT
            gender.name
        FROM
            person
        JOIN
            gender
        ON
            gender.id = person.gender_id
        WHERE
            person.id = vj.person_id
    ) AS claimed_gender,
    (
        SELECT
            ethnicity.name
        FROM
            person
        JOIN
            ethnicity
        ON
            ethnicity.id = person.ethnicity_id
        WHERE
            person.id = vj.person_id
        AND
            ethnicity.name <> 'Unanswered'
    ) AS claimed_ethnicity
FROM
    verification_job AS vj
WHERE
    status = 'queued'
OR (
        status = 'running'
    AND
        running_since IS NOT NULL
    AND
        running_since < NOW() - make_interval(secs => %(lease_seconds)s)
    AND
        reap_count < %(max_reaps)s
)
ORDER BY
    id
"""

# Take the row, or find out somebody else already did.
#
# The WHERE clause is the same test the listing made, re-made against the row
# as it stands now. That is the whole concurrency story: two workers that both
# listed a stale row race here, Postgres serialises them on the row lock, and
# the loser re-evaluates this predicate against the winner's committed row,
# finds a lease that was refreshed a moment ago, and updates nothing. The
# caller sees no returned id and does not call the classifier. A member's
# selfie is never sent twice for one claim.
#
# `reap_count` counts re-queues, not runs, so it only moves when the row was
# already 'running'. A first run is not a retry.
#
# This statement touches `verification_job` and nothing else. Ruling 2 of
# 2026-09-23: the reaper never revokes. Re-queueing a dead run must not clear,
# lower or otherwise touch person.verification_level_id or
# person.ahavah_verification_tier. A member who earned a tier keeps it, and a
# retry can only add.
Q_CLAIM_VERIFICATION_JOB = """
UPDATE
    verification_job AS vj
SET
    status = 'running',
    message = 'Our AI is checking your selfie',
    running_since = NOW(),
    reap_count = vj.reap_count + CASE WHEN vj.status = 'running' THEN 1 ELSE 0 END
WHERE
    vj.id = %(verification_job_id)s
AND (
        vj.status = 'queued'
    OR (
            vj.status = 'running'
        AND
            vj.running_since IS NOT NULL
        AND
            vj.running_since < NOW() - make_interval(secs => %(lease_seconds)s)
        AND
            vj.reap_count < %(max_reaps)s
    )
)
RETURNING
    vj.id
"""

Q_UPDATE_VERIFICATION_STATUS = """
WITH updated_verification_job AS (
    UPDATE
        verification_job
    SET
        status = %(status)s,
        message = %(message)s,
        raw_json = %(raw_json)s,
        -- Ruling 1 of 2026-09-23: this attempt's own outcome, written here
        -- as well as on the person latch below. The latch is written only
        -- on success and by whichever attempt finished LAST, while
        -- /check-verification reports the NEWEST attempt, so reading a
        -- per-job answer off the latch can describe a different run. This
        -- is written on every branch, including failure, because a failed
        -- attempt has an answer too and the latch is the one place it
        -- could never be recorded.
        verification_level_id = (
            SELECT
                id
            FROM
                verification_level
            WHERE
                name = %(verification_level_name)s
        ),
        -- The run is over, so it holds no lease. A finished row can never
        -- be read as one that blew its deadline.
        running_since = NULL
    WHERE
        id = %(verification_job_id)s
    RETURNING
        person_id,
        status
), successful_verification_job AS (
    SELECT
        person_id,
        status
    FROM
        updated_verification_job
    WHERE
        status = 'success'
), updated_person AS (
    UPDATE
        person
    SET
        verification_level_id = (
            SELECT
                id
            FROM
                verification_level
            WHERE
                name = %(verification_level_name)s
        ),
        -- Phase W cross-write: bump ahavah_verification_tier (mig 0003)
        -- alongside the upstream verification_level lookup. Cron passes
        -- target_tier='bronze' for normal Bronze runs, 'silver' for
        -- Silver bursts (mig 0012). Only ratchets UP — a Bronze run
        -- never demotes a Silver/Gold user. 'No verification' /
        -- 'Basics only' branches pass target_tier=NULL and skip the
        -- bump entirely.
        ahavah_verification_tier =
            CASE
                WHEN %(target_tier)s::TEXT IS NOT NULL
                    AND CASE ahavah_verification_tier
                            WHEN 'none' THEN 0
                            WHEN 'bronze' THEN 1
                            WHEN 'silver' THEN 2
                            WHEN 'gold' THEN 3
                        END
                        < CASE %(target_tier)s::TEXT
                            WHEN 'bronze' THEN 1
                            WHEN 'silver' THEN 2
                            WHEN 'gold' THEN 3
                            ELSE 0
                        END
                THEN %(target_tier)s::ahavah_verification_tier
                ELSE ahavah_verification_tier
            END,
        verified_age = %(verified_age)s,
        verified_gender = %(verified_gender)s,
        verified_ethnicity = %(verified_ethnicity)s
    WHERE
        id IN (SELECT person_id FROM successful_verification_job)
), updated_photo AS (
    UPDATE
        photo
    SET
        verified = (uuid = ANY(%(verified_uuids)s::TEXT[]))
    WHERE
        person_id IN (SELECT person_id FROM successful_verification_job)
)
SELECT 1
"""
