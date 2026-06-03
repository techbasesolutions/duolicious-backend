-- 0023_unsubscribe.sql
-- Add unsubscribed_at columns to waitlist_signup + beta_signup so every
-- send_* / cron path can suppress recipients who opted out. Token-backed
-- /u/<token> route stamps unsubscribed_at; the column also lets us comply
-- with Gmail/Yahoo's Feb-2024 bulk-sender requirement (one-click unsubscribe
-- must work). Idempotent.
BEGIN;

ALTER TABLE waitlist_signup
  ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ;

ALTER TABLE beta_signup
  ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS waitlist_signup_unsubscribed_idx
  ON waitlist_signup (unsubscribed_at)
  WHERE unsubscribed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS beta_signup_unsubscribed_idx
  ON beta_signup (unsubscribed_at)
  WHERE unsubscribed_at IS NOT NULL;

COMMIT;
