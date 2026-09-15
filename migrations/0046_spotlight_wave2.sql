-- migrations/0046_spotlight_wave2.sql
CREATE TABLE IF NOT EXISTS email_outbox (
  id                  bigserial PRIMARY KEY,
  campaign            text NOT NULL,
  campaign_id         text NOT NULL,
  person_id           int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  email               text NOT NULL,
  payload             jsonb NOT NULL,
  exempt              boolean NOT NULL DEFAULT false,
  unsub_scope         text NOT NULL,
  state               text NOT NULL DEFAULT 'queued',
  attempts            int NOT NULL DEFAULT 0,
  next_attempt_at     timestamptz NOT NULL DEFAULT NOW(),
  reserved_at         timestamptz,
  sent_at             timestamptz,
  provider_message_id text,
  last_error          text,
  created_at          timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (campaign, campaign_id, person_id)
);
ALTER TABLE email_outbox DROP CONSTRAINT IF EXISTS email_outbox_state_check;
ALTER TABLE email_outbox ADD CONSTRAINT email_outbox_state_check
  CHECK (state IN ('queued','reserved','accepted','acceptance_unknown','failed','skipped'));
CREATE INDEX IF NOT EXISTS email_outbox_due_idx ON email_outbox (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS email_outbox_campaign_idx ON email_outbox (campaign, campaign_id);

CREATE TABLE IF NOT EXISTS cleanup_job (
  id               bigserial PRIMARY KEY,
  kind             text NOT NULL,
  target           text NOT NULL,
  state            text NOT NULL DEFAULT 'pending',
  attempts         int NOT NULL DEFAULT 0,
  next_attempt_at  timestamptz NOT NULL DEFAULT NOW(),
  evidence         jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT NOW(),
  done_at          timestamptz
  -- No UNIQUE (kind, target) here (fix wave item 7). Spotlight object keys
  -- are content-hashed, so the same key is legitimately created, retired and
  -- created again, and an unconditional constraint would let a key be queued
  -- only once ever. 0047 adds the partial unique index over pending jobs that
  -- is actually wanted, and still carries the DROP for the constraint this
  -- line used to create, since this file may already have run elsewhere.
);
ALTER TABLE cleanup_job DROP CONSTRAINT IF EXISTS cleanup_job_kind_check;
ALTER TABLE cleanup_job ADD CONSTRAINT cleanup_job_kind_check CHECK (kind IN ('asset_delete'));
ALTER TABLE cleanup_job DROP CONSTRAINT IF EXISTS cleanup_job_state_check;
ALTER TABLE cleanup_job ADD CONSTRAINT cleanup_job_state_check CHECK (state IN ('pending','done','failed','abandoned'));
CREATE INDEX IF NOT EXISTS cleanup_job_due_idx ON cleanup_job (state, next_attempt_at);

ALTER TABLE spotlight_removal_task
  ADD COLUMN IF NOT EXISTS attempts        int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS deadline_at     timestamptz,
  ADD COLUMN IF NOT EXISTS last_error      text,
  ADD COLUMN IF NOT EXISTS evidence        jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE spotlight_removal_task SET deadline_at = created_at + interval '72 hours' WHERE deadline_at IS NULL;

ALTER TABLE publishing_queue ADD COLUMN IF NOT EXISTS image_sha256 text;
