-- migrations/0054_identity_verification_outcome.sql
--
-- Wave "verification tells the member the truth", task 5. Stripe Identity
-- stops dropping retries.
--
-- The webhook wrote a row only on `identity.verification_session.verified`.
-- Every other outcome was acknowledged with a 200 and thrown away:
-- `requires_input` (the member has to try again), `canceled`, `processing`
-- and `created`. So a member whose document came back blurry was told
-- nothing, no operator could see that anything had happened, and the only
-- evidence that a check had ever run was its absence. Measured on
-- 2026-09-20: 13 members hold ahavah_verification_tier = 'gold' and not one
-- has id_verified_country, which is the same hole seen from the other side.
--
--   id_verification_status      the last thing Stripe told us about this
--                               member's ID check. One of 'created',
--                               'processing', 'requires_input', 'canceled'.
--                               NOT 'verified': a success is recorded by
--                               ahavah_verification_tier plus
--                               id_verified_country / id_verified_at, which
--                               is what the rest of the product reads, and
--                               duplicating it here would create two places
--                               that can disagree about the same fact. This
--                               column describes attempts that granted
--                               nothing.
--
--   id_verification_status_at   when that outcome arrived. Without it the
--                               status cannot be aged, and an operator
--                               cannot tell a check that stalled an hour
--                               ago from one that stalled in June.
--
--   id_verification_session_id  the Stripe VerificationSession. This is the
--                               handle an operator needs to open the check
--                               in the Stripe dashboard. A status with no
--                               way to look it up is not an operator record.
--
--   id_verification_error_code  Stripe's last_error.code, for example
--                               'document_unverified_other'. Operators only.
--                               It is a machine code and not an explanation,
--                               and no member-facing copy repeats it: saying
--                               why a document failed, on this evidence,
--                               would be asserting something we cannot back.
--
-- Nullable throughout, so every historical row reads as "we never recorded
-- an outcome for this member", which is exactly true.
--
-- These sit on `person` rather than in a table of their own because the
-- outcome is keyed by metadata.user_id, which is a person, and because the
-- other half of this record (id_verified_country, id_verified_at, migration
-- 0003) is already here. A per-attempt history is a different thing and
-- would need its own table; nothing in this wave reads one.
--
-- The writer refuses to touch a member who is already gold, so this latch
-- can never contradict a tier the member earned. That rule lives in
-- service/identity_verification.record_identity_outcome, not in a
-- constraint, because it has to report whether it wrote.
--
-- Additive and idempotent.
ALTER TABLE person
  ADD COLUMN IF NOT EXISTS id_verification_status     TEXT,
  ADD COLUMN IF NOT EXISTS id_verification_status_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS id_verification_session_id TEXT,
  ADD COLUMN IF NOT EXISTS id_verification_error_code TEXT;

-- Partial: the overwhelming majority of members have never started an ID
-- check, and the only questions anyone asks of this column are "who is
-- stuck" and "how many checks needed another try", both of which read the
-- non-null side.
CREATE INDEX IF NOT EXISTS idx__person__id_verification_status
    ON person (id_verification_status, id_verification_status_at)
 WHERE id_verification_status IS NOT NULL;
