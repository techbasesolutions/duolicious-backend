-- 0022_auth_hardening.sql
-- Auth hardening pass (Phase W post-launch audit).
--
-- 1. duo_session.otp_attempts        — per-session counter incremented on each
--    failed /check-otp guess; the OTP nulls itself after 5 wrong attempts so
--    the brute-force budget is bounded by `5 / minted-OTP` instead of `40/day
--    per IP` (audit Auth #1).
--
-- 2. beta_signup / waitlist_signup CHECK (email = lower(btrim(email)))
--    Prevents denormalized rows from sneaking in via any future code path
--    that bypasses Pydantic's lowercase validator (audit Data Integrity #2).
--
-- 3. beta_signup.person_id INT + FK to person(id) ON DELETE SET NULL
--    Today person_id is BIGINT (no FK) while person.id is INT. Cast +
--    constrain so when pendingdeletion cron hard-deletes the person row,
--    beta_signup.person_id nulls instead of orphaning (Data Integrity #3).
--
-- Idempotent (re-runs on every deploy).

BEGIN;

-- (1) OTP attempt counter.
ALTER TABLE duo_session
  ADD COLUMN IF NOT EXISTS otp_attempts INT NOT NULL DEFAULT 0;

-- (2a) beta_signup case constraint (DO block for IF NOT EXISTS semantics).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'beta_signup_email_normalized'
  ) THEN
    ALTER TABLE beta_signup
      ADD CONSTRAINT beta_signup_email_normalized
      CHECK (email = lower(btrim(email)));
  END IF;
END$$;

-- (2b) waitlist_signup case constraint.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'waitlist_signup_email_normalized'
  ) THEN
    ALTER TABLE waitlist_signup
      ADD CONSTRAINT waitlist_signup_email_normalized
      CHECK (email = lower(btrim(email)));
  END IF;
END$$;

-- (3a) Cast beta_signup.person_id to INT to match person.id. Safe in
-- practice (person.id range is well within INT).
DO $$
BEGIN
  IF (
    SELECT data_type FROM information_schema.columns
     WHERE table_name = 'beta_signup' AND column_name = 'person_id'
  ) = 'bigint' THEN
    ALTER TABLE beta_signup
      ALTER COLUMN person_id TYPE INT USING person_id::INT;
  END IF;
END$$;

-- (3b) Add FK if not already present. ON DELETE SET NULL so the cohort row
-- survives a user delete (the email is still the natural key for re-engagement
-- analytics); ON UPDATE CASCADE for general hygiene.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'beta_signup_person_id_fkey'
  ) THEN
    ALTER TABLE beta_signup
      ADD CONSTRAINT beta_signup_person_id_fkey
      FOREIGN KEY (person_id) REFERENCES person(id)
      ON DELETE SET NULL ON UPDATE CASCADE;
  END IF;
END$$;

COMMIT;
