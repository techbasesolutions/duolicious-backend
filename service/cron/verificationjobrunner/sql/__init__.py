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
#
# A row that has burned its retries is excluded here and picked up by
# Q_ABANDONED_VERIFICATION_JOBS instead, which ends it rather than running
# it again.
#
# The LIMIT is what makes a tick interruptible. Jobs run one at a time and a
# single verify() call is bounded at 136.5 seconds, so an unbounded listing
# after an OpenAI outage would put the worker inside one `for` loop for as
# long as the backlog is deep, never returning to the top of verify_forever
# and never re-reading the queue. With a bound, the worst case tick is
# VERIFICATION_MAX_JOBS_PER_TICK x 136.5 seconds and the ordinary one is a
# few seconds; the listing then re-runs, which drops rows another worker has
# since taken and picks up whatever arrived meanwhile. Throughput is
# unchanged, because the poll is one second and ORDER BY id keeps the queue
# fair across ticks.
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
LIMIT
    %(max_jobs)s
"""

# Everything that has burned its retries and is still sitting in 'running'.
#
# Q_ELIGIBLE_VERIFICATION_JOBS refuses these and nothing else ever wrote the
# row, so before this query existed a job that died three times stayed
# 'running' until garbagerecords deleted it three days later.
# /check-verification reported `pending` the whole time and no notification
# was ever sent, which leaves the member on the web client's 90 second
# timeout as their only exit: a message that does not know the check is
# dead. The wave's own constraint is that a member is never left waiting
# with no exit, so these are ended rather than left.
Q_ABANDONED_VERIFICATION_JOBS = """
SELECT
    id,
    person_id
FROM
    verification_job
WHERE
    status = 'running'
AND
    running_since IS NOT NULL
AND
    running_since < NOW() - make_interval(secs => %(lease_seconds)s)
AND
    reap_count >= %(max_reaps)s
ORDER BY
    id
LIMIT
    %(max_jobs)s
"""

# End an abandoned run, once.
#
# The predicate is the listing's, re-made against the row as it stands now,
# so a second worker that listed the same row writes nothing and the
# returned id is what gates the notification. The member hears about a dead
# check exactly once.
#
# This touches `verification_job` and nothing else. Ruling 2 of 2026-09-23
# applies here as much as to a reap: a member who earned a tier on an
# earlier attempt keeps it, and an attempt that never produced an answer
# takes nothing away.
#
# The message is rendered on the rejected card, so it says the check did not
# finish and offers the retry. It names no reason, because there is none to
# name: nothing came back from the classifier at all.
Q_ABANDON_VERIFICATION_JOB = """
UPDATE
    verification_job
SET
    status = 'failure',
    message = %(message)s,
    verification_level_id = (
        SELECT
            id
        FROM
            verification_level
        WHERE
            name = 'No verification'
    ),
    running_since = NULL
WHERE
    id = %(verification_job_id)s
AND
    status = 'running'
AND
    running_since IS NOT NULL
AND
    running_since < NOW() - make_interval(secs => %(lease_seconds)s)
AND
    reap_count >= %(max_reaps)s
RETURNING
    id
"""

# Take the row, or find out somebody else already did.
#
# The WHERE clause is the same test the listing made, re-made against the row
# as it stands now. Two workers that both listed a stale row race here and
# Postgres serialises them on the row lock. What the loser then does depends
# entirely on the isolation level, which is why the caller pins it:
#
#   READ COMMITTED   the loser re-evaluates this predicate against the
#                    winner's committed row, finds a lease that was
#                    refreshed a moment ago, and updates nothing. The caller
#                    sees no returned id and returns.
#   REPEATABLE READ  the loser cannot re-evaluate anything, because its
#                    snapshot predates the winner's commit. PostgreSQL
#                    aborts it with `could not serialize access due to
#                    concurrent update`.
#
# This repo's connections are opened with
# `default_transaction_isolation = 'REPEATABLE READ'`
# (`database/asyncdatabase/__init__.py`), so the second behaviour is what a
# plain `api_tx()` would get: an exception out of do_verification_job, out
# of the `for` loop in verify_once, and every remaining job in the tick
# dropped. `do_verification_job` therefore claims inside
# `api_tx('read committed')`. Do not widen that back without rereading this.
#
# Either way the safety property holds: the loser does not claim and does
# not call the classifier, so a member's selfie is never sent twice for one
# claim. Only the blast radius differs.
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
    vj.id,
    -- The lease this claim just stamped. The caller carries it back as
    -- `claimed_at` and Q_UPDATE_VERIFICATION_STATUS fences on it, so a
    -- worker whose lease expired mid run cannot write its result over the
    -- result of the worker that reaped it.
    vj.running_since
"""

# Write the outcome, if this worker is still the one entitled to write it.
#
# The fence is `status = 'running' AND running_since = %(claimed_at)s`. The
# lease buys mutual exclusion on STARTING a run; on its own it buys none on
# finishing one. Worker A claims job 7 and stalls. At 151 seconds the lease
# expires, worker B reaps job 7, runs it, writes its answer and tells the
# member. Worker A then wakes with an answer of its own, and without the
# fence it overwrites B's row and notifies a second time, possibly
# contradicting what the member was already told about the same selfie. With
# the fence, A's claim stamp no longer matches the row, the update matches no
# row, `updated_jobs` comes back 0, and the caller returns without notifying,
# because somebody else has already told the member.
#
# `updated_jobs` is the count of job rows this statement actually wrote. The
# data-modifying CTEs below run to completion whether or not the final SELECT
# reads them, and each is keyed off `updated_verification_job`, so a fenced
# out write updates nothing anywhere.
#
# This runs at READ COMMITTED for the same reason the claim does: under
# REPEATABLE READ a concurrent writer on the same row aborts the loser
# instead of letting it match zero rows, and "matched nothing" is the whole
# signal the fence exists to produce.
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
    AND
        status = 'running'
    AND
        running_since = %(claimed_at)s
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
SELECT
    COUNT(*) AS updated_jobs
FROM
    updated_verification_job
"""
