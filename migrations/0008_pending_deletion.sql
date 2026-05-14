-- Phase W cutover — soft-delete with 7-day grace.
--
-- Problem: DELETE /account currently hard-deletes the person row
-- immediately. App Store reviewers (and good UX generally) expect a
-- "wait, I clicked delete by mistake" recovery window. Hard delete
-- also makes the system more dangerous to a hostile session-hijack.
--
-- Solution: flip `activated` to false on delete-request and stamp
-- `deletion_requested_at`. The user becomes invisible in /search,
-- /matches, /profile/[uuid] etc. (those queries already filter by
-- activated=true). The new `pendingdeletion` cron hard-deletes any
-- person rows where deletion_requested_at + 7 days < NOW().
--
-- Cancellation (within the grace window): user emails admin@ahavah.app;
-- admin sets deletion_requested_at = NULL and activated = TRUE.
-- A self-service /account/cancel-deletion endpoint can land later.

BEGIN;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx__person__deletion_requested_at
  ON person(deletion_requested_at)
  WHERE deletion_requested_at IS NOT NULL;

COMMIT;
