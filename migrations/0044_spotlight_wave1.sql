-- migrations/0044_spotlight_wave1.sql
-- Spotlight Wave 1: immutable content revisions, revision-bound consent,
-- feature occurrences, single-use token nonces, delivery state. Idempotent.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS spotlight_revision (
  id             bigserial PRIMARY KEY,
  request_key    text NOT NULL,
  revision       int NOT NULL,
  caption        text NOT NULL,
  photo_uuid     uuid,
  layout_version text NOT NULL DEFAULT 'v1',
  channels       text[] NOT NULL,
  participants   jsonb NOT NULL DEFAULT '[]'::jsonb,
  asset_hash     text,
  image_key      text,
  image_url      text,
  created_by     text,
  created_at     timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (request_key, revision)
);

CREATE TABLE IF NOT EXISTS spotlight_revision_consent (
  revision_id  bigint NOT NULL REFERENCES spotlight_revision(id) ON DELETE CASCADE,
  person_id    int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  role         text NOT NULL CHECK (role IN ('subject','participant','admin')),
  approved_at  timestamptz NOT NULL DEFAULT NOW(),
  nonce        text,
  PRIMARY KEY (revision_id, person_id, role)
);

CREATE TABLE IF NOT EXISTS spotlight_occurrence (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL,
  person_id   int REFERENCES person(id) ON DELETE SET NULL,
  request_key text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (request_key, person_id)
);
CREATE INDEX IF NOT EXISTS spotlight_occurrence_person_idx ON spotlight_occurrence (person_id, created_at DESC);

CREATE TABLE IF NOT EXISTS spotlight_token_nonce (
  nonce      text PRIMARY KEY,
  person_id  int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  purpose    text NOT NULL CHECK (purpose IN ('confirm','card')),
  epoch      int NOT NULL,
  issued_at  timestamptz NOT NULL DEFAULT NOW(),
  used_at    timestamptz
);
CREATE INDEX IF NOT EXISTS spotlight_token_nonce_person_idx ON spotlight_token_nonce (person_id);

ALTER TABLE person ADD COLUMN IF NOT EXISTS spotlight_consent_epoch int NOT NULL DEFAULT 0;

ALTER TABLE publishing_queue
  ADD COLUMN IF NOT EXISTS current_revision_id bigint REFERENCES spotlight_revision(id),
  ADD COLUMN IF NOT EXISTS lease_token text,
  ADD COLUMN IF NOT EXISTS cancellation_requested_at timestamptz,
  ADD COLUMN IF NOT EXISTS delivery_state text NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS post_url text;
ALTER TABLE publishing_queue DROP CONSTRAINT IF EXISTS publishing_queue_delivery_state_check;
ALTER TABLE publishing_queue ADD CONSTRAINT publishing_queue_delivery_state_check
  CHECK (delivery_state IN ('none','attempting','published','delivery_unknown','failed'));

ALTER TABLE spotlight_removal_task
  ADD COLUMN IF NOT EXISTS person_id int,
  ADD COLUMN IF NOT EXISTS request_key text;

INSERT INTO spotlight_setting (key, value) VALUES
  ('approvals_enabled','false'), ('roundup_tiles_enabled','false'), ('invites_enabled','true'),
  ('publication_enabled','false'), ('external_access_enabled','true')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION claim_spotlight_posts(max_rows int)
RETURNS SETOF publishing_queue
LANGUAGE sql
AS $$
  UPDATE publishing_queue q
     SET status = 'processing',
         delivery_state = 'attempting',
         lease_token = encode(gen_random_bytes(16), 'hex'),
         lease_until = NOW() + interval '10 minutes',
         attempts = attempts + 1,
         error = NULL,
         updated_at = NOW()
   WHERE q.id IN (
     SELECT id FROM publishing_queue
      WHERE scheduled_for <= NOW()
        AND cancellation_requested_at IS NULL
        AND (status = 'scheduled' OR (status = 'failed' AND attempts < 3))
      ORDER BY scheduled_for
      FOR UPDATE SKIP LOCKED
      LIMIT max_rows)
  RETURNING q.*;
$$;
