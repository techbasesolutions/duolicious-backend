-- Phase W cutover — web push notifications (VAPID).
--
-- Stores per-device PushSubscription objects returned by
-- swReg.pushManager.subscribe() on the frontend. One row per
-- (person_id, endpoint) — the endpoint is the unique device identifier
-- issued by the user's browser push service (FCM for Chrome/Edge/Android,
-- Apple Push for Safari/iOS PWA, Mozilla autopush for Firefox).
--
-- Cleanup: when webpush.send_notification() returns 404 or 410 the
-- backend should DELETE the row (subscription expired/unsubscribed).
-- The notifications module is responsible for that cleanup.

BEGIN;

CREATE TABLE IF NOT EXISTS push_subscription (
  id          BIGSERIAL PRIMARY KEY,
  person_id   INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  endpoint    TEXT NOT NULL,
  p256dh      TEXT NOT NULL,
  auth        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (person_id, endpoint)
);

CREATE INDEX IF NOT EXISTS idx__push_subscription__person_id
  ON push_subscription(person_id);

COMMIT;
