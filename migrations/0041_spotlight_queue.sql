-- migrations/0041_spotlight_queue.sql
CREATE TABLE IF NOT EXISTS publishing_queue (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_key         text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('welcome','roundup','member_of_week','highlight')),
  subject_person_id   int REFERENCES person(id) ON DELETE SET NULL,
  platform            text NOT NULL CHECK (platform IN ('facebook','instagram')),
  caption             text NOT NULL DEFAULT '',
  image_url           text,
  image_key           text,
  scheduled_for       timestamptz,
  status              text NOT NULL DEFAULT 'review' CHECK (status IN ('awaiting_member','awaiting_render','review','scheduled','processing','published','failed','cancelled')),
  lease_until         timestamptz,
  attempts            int NOT NULL DEFAULT 0,
  external_post_id    text,
  error               text,
  member_approved_at  timestamptz,
  approved_photo_uuid uuid,
  created_by          text,
  created_at          timestamptz NOT NULL DEFAULT NOW(),
  updated_at          timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (request_key, platform)
);
CREATE INDEX IF NOT EXISTS publishing_queue_due_idx ON publishing_queue (status, scheduled_for);
CREATE INDEX IF NOT EXISTS publishing_queue_subject_idx ON publishing_queue (subject_person_id);

CREATE TABLE IF NOT EXISTS spotlight_removal_task (
  id               bigserial PRIMARY KEY,
  queue_id         uuid REFERENCES publishing_queue(id) ON DELETE CASCADE,
  platform         text NOT NULL,
  external_post_id text,
  reason           text NOT NULL,
  done_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spotlight_setting (
  key        text PRIMARY KEY,
  value      text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT NOW()
);
INSERT INTO spotlight_setting (key, value) VALUES ('scheduler_enabled','false'), ('auto_welcome','false'), ('auto_roundup','false')
ON CONFLICT (key) DO NOTHING;

ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS signup_person_id int REFERENCES person(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION claim_spotlight_posts(max_rows int)
RETURNS SETOF publishing_queue
LANGUAGE sql
AS $$
  UPDATE publishing_queue q
     SET status = 'processing',
         lease_until = NOW() + interval '10 minutes',
         attempts = attempts + 1,
         error = NULL,
         updated_at = NOW()
   WHERE q.id IN (
     SELECT id FROM publishing_queue
      WHERE status = 'scheduled' AND scheduled_for <= NOW()
      ORDER BY scheduled_for
      FOR UPDATE SKIP LOCKED
      LIMIT max_rows)
  RETURNING q.*;
$$;
