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

-- Backfill is date-pinned to the snapshot at migration-author time so that
-- when this migration re-runs on every deploy (per the project pattern) it
-- does NOT suppress reengagement for beta rows created AFTER 2026-05-27 —
-- those legitimate new rows should be eligible for the auto-cron's email.
UPDATE beta_signup
   SET reengagement_sent_at = NOW()
 WHERE reengagement_sent_at IS NULL
   AND created_at < '2026-05-27'::timestamptz;

COMMIT;
