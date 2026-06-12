-- 0027_waitlist_welcome_tracking.sql
-- Auditability for the fire-and-forget waitlist welcome email. Previously
-- send_waitlist_welcome_async fired with no trace, so "did this signup get
-- the welcome?" was unanswerable. Record the send time + the Resend message
-- id (so delivery can be looked up per-recipient in the Resend dashboard).
-- Idempotent (the deploy re-runs every migration). No backfill: rows that
-- predate this migration were sent (or not) untracked, and we don't want to
-- assert a send time we can't prove.
BEGIN;

ALTER TABLE waitlist_signup
  ADD COLUMN IF NOT EXISTS welcome_sent_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS welcome_message_id TEXT;

COMMIT;
