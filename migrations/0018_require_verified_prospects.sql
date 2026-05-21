-- 0018_require_verified_prospects.sql
-- Separate the "Require verified matches" SEARCHER preference from the
-- anti-abuse/location flag person.verification_required (which the chat
-- send-gate + the global search hide read). Conflating them blocked users who
-- enabled the preference from messaging + hid them. Idempotent (the deploy
-- re-runs every migration every time and swallows errors).
BEGIN;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS require_verified_prospects BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
