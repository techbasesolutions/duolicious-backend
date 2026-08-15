-- 0038: onboardee activity timestamp (F16).
-- Q_MAYBE_DELETE_ONBOARDEE wiped wizard state older than 1 hour by
-- created_at, but no field write refreshed anything, so a slow
-- onboarder re-verifying an OTP lost name/DOB/photos. Same bug class
-- as the zombie-pass fix: a window whose clock nothing restarts.
ALTER TABLE onboardee ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
