-- Phase 3 Task 3.1 — verification tier enum + person columns.
--
-- Three tiers (Bumpy-style "self-select your trust level"):
--   bronze : the upstream Duolicious fork's existing selfie+gender+age+ethnicity check
--   silver : AWS Amplify Face Liveness (anti-spoof, screen-color challenge)
--   gold   : Stripe Identity (gov ID + face match)
--
-- The frontend renders a tier-coloured badge next to the user's name on
-- profile cards, swipe deck, and chat header. A "Verified only" search
-- toggle (Phase 3 Task 3.2 Step 5) becomes a premium revenue lever.
--
-- Naming note: the upstream Duolicious schema already has a
-- `verification_level` TABLE (lookup table referenced by
-- person.verification_level_id and person.privacy_verification_level_id
-- foreign keys). Postgres types and table row-types share a namespace,
-- so the new ENUM gets a distinct name `ahavah_verification_tier` to
-- avoid the silent CREATE TYPE skip + downstream "malformed record
-- literal" error. Phase W F.2 caught this on first deploy.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typname = 'ahavah_verification_tier'
      AND n.nspname = 'public'
      AND t.typtype = 'e'
  ) THEN
    CREATE TYPE ahavah_verification_tier AS ENUM ('none','bronze','silver','gold');
  END IF;
END$$;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS ahavah_verification_tier  ahavah_verification_tier NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS id_verified_country       CHAR(2),
  ADD COLUMN IF NOT EXISTS id_verified_at            TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx__person__ahavah_verification_tier
  ON person (ahavah_verification_tier)
  WHERE ahavah_verification_tier <> 'none';   -- partial: only verified users

COMMIT;
