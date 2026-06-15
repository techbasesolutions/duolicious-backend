-- 0029_notification_channel_prefs.sql
-- Phase 2 of the notification redesign: extend notification_preference from
-- a push-only model (mig 0013: push_matches/messages/likes/weekly_digest) to
-- a per-event x per-channel matrix. Adds email_* columns and the two new
-- events (verification, profile_views). push_weekly_digest is retained but
-- unused (deferred feature) — no destructive drop.
-- Idempotent (IF NOT EXISTS) so it is safe to re-run on every deploy.
-- Defaults mirror the approved matrix: messages/matches/verification default
-- ON on both channels; likes/profile_views default OFF (opt-in).
BEGIN;

ALTER TABLE notification_preference
  ADD COLUMN IF NOT EXISTS email_messages       BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_matches        BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_likes          BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS push_verification    BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS email_verification   BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS push_profile_views   BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS email_profile_views  BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
