-- Phase 3 Task 3.1 — verification level enum + person columns.
--
-- Three tiers (Bumpy-style "self-select your trust level"):
--   bronze : the upstream Duolicious fork's existing selfie+gender+age+ethnicity check
--   silver : AWS Amplify Face Liveness (anti-spoof, screen-color challenge)
--   gold   : Stripe Identity (gov ID + face match)
--
-- The frontend renders a tier-coloured badge next to the user's name on
-- profile cards, swipe deck, and chat header. A "Verified only" search
-- toggle (Phase 3 Task 3.2 Step 5) becomes a premium revenue lever.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'verification_level') THEN
    CREATE TYPE verification_level AS ENUM ('none','bronze','silver','gold');
  END IF;
END$$;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS verification_level   verification_level NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS id_verified_country  CHAR(2),
  ADD COLUMN IF NOT EXISTS id_verified_at       TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx__person__verification_level
  ON person (verification_level)
  WHERE verification_level <> 'none';   -- partial index: only verified users

COMMIT;
