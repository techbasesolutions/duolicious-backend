-- 0024_referrals.sql
-- Beta-tester referral system: per-tester reusable code, attribution
-- row per invitee, extended token_ledger reason enum. Idempotent.
-- See docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
BEGIN;

ALTER TABLE beta_signup
  ADD COLUMN IF NOT EXISTS referral_code           TEXT,
  ADD COLUMN IF NOT EXISTS referral_intro_sent_at  TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS beta_signup_referral_code_uidx
  ON beta_signup (referral_code)
  WHERE referral_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS referral (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  inviter_email   TEXT NOT NULL REFERENCES beta_signup(email) ON DELETE CASCADE,
  invitee_email   TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('pending','graduated','credited'))
                  DEFAULT 'pending',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  graduated_at    TIMESTAMPTZ,
  credited_at     TIMESTAMPTZ,
  UNIQUE (invitee_email),
  CHECK (inviter_email <> invitee_email)
);

CREATE INDEX IF NOT EXISTS referral_inviter_status_idx
  ON referral (inviter_email, status);

CREATE INDEX IF NOT EXISTS referral_status_idx
  ON referral (status)
  WHERE status IN ('pending','graduated');

-- Extend token_ledger.reason enum to allow 'referral'. The constraint
-- name `token_ledger_reason_check` is the live name (verified via
-- `SELECT conname FROM pg_constraint WHERE conrelid = 'token_ledger'::regclass`).
ALTER TABLE token_ledger
  DROP CONSTRAINT IF EXISTS token_ledger_reason_check;

ALTER TABLE token_ledger
  ADD CONSTRAINT token_ledger_reason_check
  CHECK (reason IN (
    'purchase','subscription_stipend','reveal_liker','super_like',
    'day_pass','boost','refund','referral',
    -- 'rewind' (0015/0028) omitted here originally, but this migration
    -- re-runs on every deploy and drops the constraint first, so the
    -- re-add failed every time against the 19 live rewind rows
    -- (2026-07-22). Keep this a superset of every allowed reason.
    'rewind'
  ));

COMMIT;
