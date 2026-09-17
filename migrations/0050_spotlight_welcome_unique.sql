-- migrations/0050_spotlight_welcome_unique.sql
-- Wave 3d Task 3 (acceptance Runtime 8a). At most one live welcome per member
-- and platform. `post_growth_spotlight_welcome` checks for a non-cancelled
-- welcome and then creates one, but that check is a read every racing worker
-- passes together: eight concurrent calls for one member created four request
-- keys and sent four E4 invites. This index is the same guard in database
-- form, per platform row, so the losers fail on insert and the route answers
-- them 409.
--
-- Deploy pre-check. Run this on production first; it must return zero rows:
--
--   SELECT subject_person_id, platform, count(*)
--     FROM publishing_queue
--    WHERE kind = 'welcome' AND status <> 'cancelled'
--      AND subject_person_id IS NOT NULL
--    GROUP BY 1, 2 HAVING count(*) > 1;
--
-- `subject_person_id IS NOT NULL` is deliberate. A deleted member's rows keep
-- a NULL subject (ON DELETE SET NULL), a unique index treats NULLs as
-- distinct, so those rows never block the index and must not be reported as
-- conflicts either.
--
-- The block below runs the same check and refuses with a message naming each
-- conflict, rather than letting CREATE UNIQUE INDEX fail opaquely. Fix any
-- conflict by cancelling the extra welcome request, then apply again.
--
-- One transaction, with publishing_queue held in SHARE mode from before the
-- check until the index exists. SHARE blocks every insert, update and delete
-- but still allows reads, so no welcome can be created or un-cancelled
-- between a check that passed and the index build. psql runs this file with
-- ON_ERROR_STOP, so a refusal ends the session and the transaction rolls
-- back with nothing applied. apply-deploy-migrations.sh holds its advisory
-- lock at session level, which survives the COMMIT (most of 0001 to 0031
-- already carry their own BEGIN/COMMIT and apply through the same script).
--
-- Idempotent.
BEGIN;

LOCK TABLE publishing_queue IN SHARE MODE;

DO $$
DECLARE
  conflicts text;
BEGIN
  SELECT string_agg('person ' || d.subject_person_id || ' on ' || d.platform || ' has ' || d.n,
                    '; ' ORDER BY d.subject_person_id, d.platform)
    INTO conflicts
    FROM (SELECT subject_person_id, platform, count(*) AS n
            FROM publishing_queue
           WHERE kind = 'welcome' AND status <> 'cancelled'
             AND subject_person_id IS NOT NULL
           GROUP BY 1, 2 HAVING count(*) > 1) d;
  IF conflicts IS NOT NULL THEN
    RAISE EXCEPTION USING
      MESSAGE = '0050 refused: publishing_queue holds more than one live welcome for the same member and platform: '
                || conflicts,
      HINT = 'Cancel the extra welcome requests, then apply 0050 again.';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS publishing_queue_one_live_welcome
    ON publishing_queue (subject_person_id, platform)
 WHERE kind = 'welcome' AND status <> 'cancelled';

COMMIT;
