-- Phase W: per-event notification preferences.
--
-- Today push goes out unconditionally (service/decisions sends on every
-- mutual like, service/chat/messagestorage sends on every incoming
-- message). The /settings/notifications page surfaced fake toggles
-- with no backend. This migration adds the persistent state that
-- backs four real toggles:
--
--   push_matches       — fire push on mutual like (default ON)
--   push_messages      — fire push on new chat message (default ON)
--   push_likes         — fire push on incoming like (default OFF;
--                        premium-gated read surface, so the trigger
--                        is gated to premium users separately)
--   push_weekly_digest — weekly summary email/push (default OFF;
--                        feature not yet built, column reserves
--                        the toggle for when it ships)
--
-- One row per person, lazily inserted on first PATCH. Send path
-- treats missing row as "all defaults" so legacy users get matches +
-- messages by default without a backfill.

BEGIN;

CREATE TABLE IF NOT EXISTS notification_preference (
  person_id          INTEGER PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
  push_matches       BOOLEAN NOT NULL DEFAULT TRUE,
  push_messages      BOOLEAN NOT NULL DEFAULT TRUE,
  push_likes         BOOLEAN NOT NULL DEFAULT FALSE,
  push_weekly_digest BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
