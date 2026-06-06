-- 0025_referral_link_click.sql
-- Click tracking for /i/<code> referral landings. Decoupled from the
-- referral table because clicks happen with or without a subsequent
-- signup; this lets us answer "are people clicking the links" without
-- conflating with "are they signing up". Idempotent.
BEGIN;

CREATE TABLE IF NOT EXISTS referral_link_click (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code            TEXT NOT NULL,
  inviter_email   TEXT,
  well_formed     BOOLEAN NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  user_agent_class TEXT,
  user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS referral_link_click_code_idx
  ON referral_link_click (code, created_at);

CREATE INDEX IF NOT EXISTS referral_link_click_inviter_idx
  ON referral_link_click (inviter_email, created_at)
  WHERE inviter_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS referral_link_click_created_idx
  ON referral_link_click (created_at DESC);

COMMIT;
