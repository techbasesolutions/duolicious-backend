-- 0026_admin_audit_log.sql
-- Append-only log of every destructive admin action. Idempotent.
-- See docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md.
BEGIN;

CREATE TABLE IF NOT EXISTS admin_audit_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_email   TEXT NOT NULL,
  actor_uuid    UUID NOT NULL,
  action        TEXT NOT NULL,
  target_email  TEXT,
  target_uuid   UUID,
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS admin_audit_log_actor_created_idx
  ON admin_audit_log (actor_email, created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_log_target_created_idx
  ON admin_audit_log (target_email, created_at DESC)
  WHERE target_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS admin_audit_log_action_created_idx
  ON admin_audit_log (action, created_at DESC);

COMMIT;
