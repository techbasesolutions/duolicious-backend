-- 0020_beta_signup.sql
-- Beta-tester cohort (2026-05-26). Onboarders who opt in on the completion
-- screen. Idempotent (the deploy re-runs every migration and swallows errors).
-- email is the natural key (lower-cased + trimmed by the service); person_id
-- links the graduated account (nullable to tolerate opt-in before graduation).
BEGIN;

CREATE TABLE IF NOT EXISTS beta_signup (
  email       TEXT         PRIMARY KEY,
  person_id   BIGINT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMIT;
