-- 0017_waitlist_signup.sql
-- Pre-signup waitlist capture. Idempotent (the deploy re-runs every migration
-- every time and swallows errors). email is the natural key (lower-cased +
-- trimmed by the API). answers holds the onboarding-shaped payload so a
-- magic-link launch flow can prefill onboarding later.
BEGIN;

CREATE TABLE IF NOT EXISTS waitlist_signup (
  email       TEXT         PRIMARY KEY,
  answers     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMIT;
