-- Phase 5 Task 5.2 — server-side IAP entitlements.
--
-- Two columns on `person` plus an append-only `entitlement_event` ledger
-- for replay-protected RevenueCat webhook handling. We deliberately
-- denormalize the "current entitlements" set onto person rather than
-- always-deriving from the ledger, because the gate-check on every
-- request needs to be an O(1) array lookup and not a ledger fold.
--
-- The ledger is the source of truth for *audit*; the array is the
-- source of truth for *enforcement*. Reconciliation cron (Task 5.2
-- Step 5) periodically reconciles the two against RevenueCat's REST API.

BEGIN;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS entitlements              TEXT[]        NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS subscription_expires_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx__person__entitlements
  ON person USING GIN (entitlements)
  WHERE entitlements <> '{}';   -- partial: skip the (huge) free-tier population

CREATE TABLE IF NOT EXISTS entitlement_event (
  event_id      TEXT        PRIMARY KEY,            -- RevenueCat event UUID
  event_type    TEXT        NOT NULL,               -- INITIAL_PURCHASE / RENEWAL / EXPIRATION / etc.
  app_user_id   TEXT        NOT NULL,               -- our person.id, stringified
  payload       JSONB       NOT NULL,
  received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx__entitlement_event__app_user_id
  ON entitlement_event (app_user_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx__entitlement_event__received_at
  ON entitlement_event (received_at DESC);

COMMIT;
