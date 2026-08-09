-- Member referrals for everyone + early-member Premium (2026-08-09).
--
-- Referral codes previously lived ONLY on beta_signup, so organic
-- members (everyone who joined after the waitlist era) had no invite
-- link and could never refer anyone. Codes now live on person too;
-- code resolution checks person first, then beta_signup (legacy codes
-- keep working). Minted at finish-onboarding for new members and
-- backfilled for existing ones.
--
-- Idempotent: safe on the re-run-every-deploy path.

ALTER TABLE person ADD COLUMN IF NOT EXISTS referral_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_person_referral_code
    ON person (referral_code)
 WHERE referral_code IS NOT NULL;

-- referral.inviter_email used to REFERENCE beta_signup(email), which
-- made it impossible for anyone outside the beta list to be an
-- inviter. Referrals now stand alone on plain emails: inviters can be
-- any member (person.referral_code) and legacy beta codes still
-- resolve. ON DELETE CASCADE semantics are not needed; referral rows
-- are an append-mostly attribution ledger.
ALTER TABLE referral DROP CONSTRAINT IF EXISTS referral_inviter_email_fkey;
