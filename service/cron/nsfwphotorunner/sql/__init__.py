Q_50_UNCHECKED_PHOTOS = """
SELECT
    uuid
FROM
    photo
WHERE
    nsfw_score IS NULL
LIMIT
    50
"""

Q_SET_NSFW_SCORE = """
UPDATE
    photo
SET
    nsfw_score = %(nsfw_score)s,
    -- Auto-classify on the same UPDATE so photos don't sit in
    -- 'pending' forever waiting on a manual admin click.
    --   score <  0.30           -> approved
    --   score >= 0.70           -> rejected
    --   0.30 <= score < 0.70    -> manual_review
    --   score = -1.0 (download failed) -> leave at 'pending', let
    --                              the next cron tick retry the
    --                              download.
    moderation_status = CASE
        WHEN %(nsfw_score)s < 0     THEN moderation_status
        WHEN %(nsfw_score)s < 0.30  THEN 'approved'::photo_moderation_status
        WHEN %(nsfw_score)s >= 0.70 THEN 'rejected'::photo_moderation_status
        ELSE                              'manual_review'::photo_moderation_status
    END,
    moderated_at = CASE
        WHEN %(nsfw_score)s < 0 THEN moderated_at
        ELSE NOW()
    END
WHERE
    uuid = %(uuid)s
"""
