-- 0021_beta_reengagement_sent_at.sql
-- Tracks when the auto-reengagement cron sent the "you joined the beta but
-- haven't completed onboarding" follow-up so each beta tester gets it at
-- most once. Idempotent (the deploy re-runs every migration). All existing
-- rows are backfilled to NOW() because every beta_signup row at the time of
-- this migration has ALREADY been emailed manually — we don't want the cron
-- to re-blast them on first run.
BEGIN;

ALTER TABLE beta_signup
  ADD COLUMN IF NOT EXISTS reengagement_sent_at TIMESTAMPTZ;

UPDATE beta_signup
   SET reengagement_sent_at = NOW()
 WHERE reengagement_sent_at IS NULL;

COMMIT;
