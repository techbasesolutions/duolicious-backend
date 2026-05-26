-- 0019_feedback.sql
-- Public feedback capture (2026-05-26). Durable store so a Resend failure can
-- never silently lose feedback. Idempotent (the deploy re-runs every migration
-- every time and swallows errors). Append-only; no natural key.
BEGIN;

CREATE TABLE IF NOT EXISTS feedback (
  id          BIGSERIAL    PRIMARY KEY,
  category    TEXT         NOT NULL,
  message     TEXT         NOT NULL,
  email       TEXT,
  path        TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS feedback_created_at_idx ON feedback (created_at);

COMMIT;
