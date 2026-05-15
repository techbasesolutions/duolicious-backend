Q_QUEUED_VERIFICATION_JOBS = """
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
"""

Q_SET_VERIFICATION_JOB_RUNNING = """
UPDATE
    verification_job
SET
    status = 'running',
    message = 'Our AI is checking your selfie'
WHERE
    id = %(verification_job_id)s
"""

Q_UPDATE_VERIFICATION_STATUS = """
WITH updated_verification_job AS (
    UPDATE
        verification_job
    SET
        status = %(status)s,
        message = %(message)s,
        raw_json = %(raw_json)s
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
