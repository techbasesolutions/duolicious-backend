-- migrations/0047_cleanup_job_partial_unique.sql
-- Wave 2 Task 5 fix round 1, ruling 1. Migration 0046 gave cleanup_job an
-- unconditional UNIQUE (kind, target). That is the wrong idempotency point:
-- spotlight object keys are content-hashed, so the SAME key can legitimately
-- be created, retired, re-created and retired again (a re-upload of identical
-- bytes produces an identical key). Under the unconditional constraint the
-- first job for a key is the only one there will ever be -- once it is done,
-- abandoned or failed, no producer can ever queue that key again, and the
-- second lifetime's object is never cleaned up.
--
-- The uniqueness that is actually wanted is "at most one OPEN job per key":
-- a partial unique index over state = 'pending'. A done/abandoned/failed row
-- stays in the table as the record of what happened and blocks nothing.
-- `enqueue_asset_delete` infers this index with
-- ON CONFLICT (kind, target) WHERE state = 'pending' DO NOTHING.
--
-- Idempotent.
ALTER TABLE cleanup_job DROP CONSTRAINT IF EXISTS cleanup_job_kind_target_key;
DROP INDEX IF EXISTS cleanup_job_kind_target_key;
CREATE UNIQUE INDEX IF NOT EXISTS cleanup_job_pending_target_uidx
  ON cleanup_job (kind, target) WHERE state = 'pending';
