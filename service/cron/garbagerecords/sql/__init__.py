from commonsql import Q_UPDATE_VERIFICATION_LEVEL_ASSIGN

Q_DELETE_GARBAGE_RECORDS = f"""
WITH q1 AS (
    DELETE FROM
        banned_person_admin_token
    WHERE
        expires_at < NOW()
    RETURNING
        1
), q2 AS (
    DELETE FROM
        deleted_photo_admin_token
    WHERE
        expires_at < NOW()
    RETURNING
        1
), q3 AS (
    DELETE FROM
        banned_person
    WHERE
        expires_at < NOW()
    RETURNING
        1
), q4 AS (
    DELETE FROM
        duo_session
    WHERE
        session_expiry < NOW()
    RETURNING
        1
), q5 AS (
    DELETE FROM
        onboardee
    WHERE
        created_at < NOW() - INTERVAL '1 week'
    RETURNING
        email
), q6 AS (
    DELETE FROM
        verification_job
    WHERE
        expires_at < NOW()
    RETURNING
        photo_uuid AS uuid
), nsfw_staged AS (
    -- F19: stage uuids for the CDN cleaner (same undeleted_photo queue /
    -- ON CONFLICT DO NOTHING semantics as the pendingdeletion F11 fix)
    -- BEFORE the hard delete below, so a false positive on a 34-member
    -- community's photo is at least cleaned off the CDN, not leaked.
    INSERT INTO
        undeleted_photo (uuid)
    SELECT
        uuid
    FROM
        photo
    WHERE
        nsfw_score > 0.8
    ON CONFLICT DO NOTHING
), q7 AS (
    DELETE FROM
        photo
    WHERE
        nsfw_score > 0.8
    RETURNING
        uuid, person_id, nsfw_score AS score
), nsfw_removed AS (
    -- F19: per-photo detail (person name + uuid + score) for the admin
    -- notice, so a false positive is seen the hour it happens instead of
    -- silently vanishing.
    SELECT
        q7.uuid,
        q7.score,
        person.name AS person_name
    FROM
        q7
    JOIN
        person
    ON
        person.id = q7.person_id
), each_deleted_photo AS (
    SELECT
        onboardee_photo.uuid
    FROM
        onboardee_photo
    JOIN
        q5
    ON
        onboardee_photo.email = q5.email

    UNION

    SELECT uuid FROM q6

    UNION

    SELECT uuid FROM q7
), q8 AS (
    DELETE FROM
        export_data_token
    WHERE
        expires_at < NOW()
    RETURNING
        1
), q9 AS (
    INSERT INTO
        undeleted_photo (uuid)
    SELECT
        uuid
    FROM
        each_deleted_photo
    -- nsfw_staged already inserted q7's uuids above; without this, the
    -- overlap would hit the undeleted_photo PK and abort every deletion
    -- in this sweep whenever an NSFW photo is removed.
    ON CONFLICT DO NOTHING
    RETURNING
        1
), q10 AS (
    UPDATE
        person
    SET
        {Q_UPDATE_VERIFICATION_LEVEL_ASSIGN},

        -- The account's last event was likely `added_photo_uuid`, but we just
        -- removed the photo which the event referred to.
        last_event_time = sign_up_time,
        last_event_name = 'joined',
        last_event_data = '{{}}'  -- Escape python's f-string syntax
    WHERE
        id IN (SELECT person_id FROM q7)
)
SELECT
    SUM(n) AS count,
    -- F19: per-photo detail for the admin notice. NULL (not '[]') when
    -- nothing was removed, so the cron can gate the email on "any rows".
    (SELECT jsonb_agg(jsonb_build_object(
        'uuid', uuid,
        'score', score,
        'person_name', person_name
    )) FROM nsfw_removed) AS nsfw_removed
FROM (
    SELECT 1 AS n FROM q1 UNION ALL
    SELECT 1 AS n FROM q2 UNION ALL
    SELECT 1 AS n FROM q3 UNION ALL
    SELECT 1 AS n FROM q4 UNION ALL
    SELECT 1 AS n FROM q5 UNION ALL
    SELECT 1 AS n FROM q6 UNION ALL
    SELECT 1 AS n FROM q7 UNION ALL
    SELECT 1 AS n FROM q8
) AS t(n)
"""
