-- Wave 3d Task 4 (acceptance 8c). Old work is never starved.
--
-- The render tick is handed at most 200 rows. Newest first, 205 newer cards
-- whose render kept failing hid an older card for as long as they failed.
-- The listing is now oldest first, and a failed render backs its card off so
-- a card that keeps failing cannot fill the listing ahead of work that would
-- render:
--
--   render_attempts         failures reported since the last successful attach
--   render_next_attempt_at  the tick's listing skips the row until this passes
--   render_error            the last reported reason, sanitised by the route
--                           (no URL, at most 200 characters)
--
-- `render_error` is kept apart from `error`, which publishing and
-- cancellation own (lease_expired, approval_expired, purged, a platform
-- failure) and which the Growth tab reads as a publish failure.
--
-- Additive only: every existing row starts with no failures and no backoff,
-- so nothing changes for it until a render is reported failed. Idempotent.
ALTER TABLE publishing_queue
  ADD COLUMN IF NOT EXISTS render_attempts        int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS render_next_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS render_error           text;
