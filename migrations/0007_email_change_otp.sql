-- Phase W cutover — change-email flow.
--
-- Backend gap surfaced 2026-05-14: /settings/account had no way to change
-- a user's email beyond "contact admin." This migration adds the columns
-- the change-email request/verify endpoints need.
--
-- Flow:
--   1. POST /account/change-email-request {new_email}
--      → backend writes (pending_email, pending_email_otp_hash, expiry)
--        on the person row, sends OTP to new_email via Resend.
--   2. POST /account/change-email-verify {otp}
--      → backend hashes otp, matches against pending_email_otp_hash,
--        checks expiry, and on success: swaps email = pending_email,
--        clears the pending fields. Sessions stay valid (the user is
--        still authenticated by their existing session_token).
--
-- Anti-abuse: rate limit at the route layer; a single in-flight pending
-- change per user (any new POST /change-email-request overwrites the
-- previous one). Expiry caps how long a leaked OTP is useful.

BEGIN;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS pending_email TEXT,
  ADD COLUMN IF NOT EXISTS pending_email_otp_hash TEXT,
  ADD COLUMN IF NOT EXISTS pending_email_otp_expiry TIMESTAMPTZ;

-- Defensive: only one user can ever have a given email. The pending
-- email reservation also needs uniqueness so a second user can't stash
-- the same target. Partial unique index excludes NULLs.
CREATE UNIQUE INDEX IF NOT EXISTS uniq__person__pending_email
  ON person(pending_email)
  WHERE pending_email IS NOT NULL;

COMMIT;
