-- Phase 4 Task 4.0 — photo moderation status tier on duolicious's photo table.
--
-- Audit correction (Task 0.0): we extend the existing ONNX pipeline at
-- antiabuse/antiporn/ rather than wiring AWS Rekognition. The ONNX scorer
-- already populates `photo.nsfw_score`; this migration adds the *verdict*
-- tier (approved / manual_review / rejected / pending) so borderline-score
-- photos can route to a human-review queue instead of being silently
-- dropped or silently approved.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'photo_moderation_status') THEN
    CREATE TYPE photo_moderation_status AS ENUM (
      'pending',         -- newly uploaded; not yet scored
      'approved',        -- scored below MANUAL_REVIEW threshold
      'manual_review',   -- borderline score; awaiting human review
      'rejected'         -- scored above REJECT threshold
    );
  END IF;
END$$;

ALTER TABLE photo
  ADD COLUMN IF NOT EXISTS moderation_status     photo_moderation_status NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS moderation_labels     TEXT[]      NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS moderation_confidence REAL,
  ADD COLUMN IF NOT EXISTS moderated_at          TIMESTAMPTZ;

-- Partial index — we only ever scan for unfinished work, never for the
-- (much larger) approved population. `approved` is the path-of-success
-- and is filtered by other queries via `verified=TRUE` already.
CREATE INDEX IF NOT EXISTS idx__photo__moderation_status_unresolved
  ON photo (moderation_status)
  WHERE moderation_status IN ('pending', 'manual_review');

-- The same applies to onboardee_photo, where the user uploads photos
-- BEFORE the row is moved to `photo` on signup completion. We mirror the
-- column so a borderline upload can be flagged at onboarding time and
-- the user warned to swap it before signing up.
ALTER TABLE onboardee_photo
  ADD COLUMN IF NOT EXISTS moderation_status     photo_moderation_status NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS moderation_labels     TEXT[]      NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS moderation_confidence REAL,
  ADD COLUMN IF NOT EXISTS moderated_at          TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx__onboardee_photo__moderation_status_unresolved
  ON onboardee_photo (moderation_status)
  WHERE moderation_status IN ('pending', 'manual_review');

COMMIT;
