-- 0030_push_subscription_failures.sql
-- Phase 3 reliability: track consecutive push failures so a subscription
-- that keeps failing (transient errors that never recover) gets pruned, not
-- just the 404/410 revoked/expired ones. This keeps "has a live subscription"
-- close to "actually reachable" — the email-fallback decision depends on that
-- approximation. Reset to 0 on any successful send.
-- Idempotent.
BEGIN;

ALTER TABLE push_subscription
  ADD COLUMN IF NOT EXISTS consecutive_failures INT NOT NULL DEFAULT 0;

COMMIT;
