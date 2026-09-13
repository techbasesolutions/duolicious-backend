-- migrations/0039_community_spotlight.sql
-- Community Spotlight, Phase A. Idempotent.
ALTER TABLE person
  ADD COLUMN IF NOT EXISTS spotlight_opt_in boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS spotlight_opt_in_at timestamptz,
  ADD COLUMN IF NOT EXISTS spotlight_last_featured_at timestamptz,
  ADD COLUMN IF NOT EXISTS reinvite_sent_at timestamptz,
  ADD COLUMN IF NOT EXISTS spotlight_ref text;

CREATE TABLE IF NOT EXISTS email_send_log (
  id          bigserial PRIMARY KEY,
  person_id   int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  campaign    text NOT NULL,
  campaign_id text NOT NULL,
  message_id  text,
  sent_at     timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (person_id, campaign, campaign_id)
);
CREATE INDEX IF NOT EXISTS email_send_log_person_sent_idx ON email_send_log (person_id, sent_at DESC);

CREATE TABLE IF NOT EXISTS campaign_link (
  key               text PRIMARY KEY,
  kind              text NOT NULL,
  target_url        text NOT NULL,
  subject_person_id int REFERENCES person(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaign_click (
  id         bigserial PRIMARY KEY,
  link_key   text NOT NULL REFERENCES campaign_link(key) ON DELETE CASCADE,
  clicked_at timestamptz NOT NULL DEFAULT NOW(),
  ua_class   text NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS campaign_click_key_idx ON campaign_click (link_key, clicked_at DESC);
