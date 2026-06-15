-- 0031_profile_view.sql
-- Phase 3: throttle store for "someone viewed your profile" notifications.
-- One row per (viewer, viewed); last_notified_at gates the notification to at
-- most once per 24h per pair. Rows are only written when the viewed person has
-- opted into profile-view notifications, so the default-off majority incur no
-- writes on the hot profile-view path. Idempotent.
BEGIN;

CREATE TABLE IF NOT EXISTS profile_view (
  viewer_id        INTEGER     NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  viewed_id        INTEGER     NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  last_notified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (viewer_id, viewed_id)
);

COMMIT;
