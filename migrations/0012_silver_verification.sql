-- Phase 3 Task 3.1 — Silver verification tier (free, classifier-only).
--
-- Silver = a 3-frame challenge. The user captures three selfies with
-- different head poses; the existing Bronze classifier (GPT-4.1 vision)
-- runs ONCE on the first frame, with the other two frames included as
-- additional `claimed_uuids` so the classifier confirms all three
-- selfies show the same person AND the profile photos do too.
--
-- Without this column the cron's existing single-photo path fires.
-- When `silver_burst_uuids` is set:
--   - cron appends those UUIDs to the claimed_uuids list it passes to
--     verify(), requiring all of them to come back as "same person"
--   - on success, ahavah_verification_tier is set to 'silver' instead
--     of 'bronze'
--
-- No new ML, no new vendor, no recurring cost beyond the existing
-- Bronze OpenAI per-call spend (Silver is one classifier call with two
-- extra `low`-detail images attached — marginal token bump).

BEGIN;

ALTER TABLE verification_job
  ADD COLUMN IF NOT EXISTS silver_burst_uuids TEXT[] DEFAULT NULL;

COMMIT;
