-- 0014_token_economy.sql
-- Token economy: append-only ledger + reveal idempotency + boost TTL.
-- The ledger is the single source of truth for balance — compute via
-- SUM(delta) per person_id. No denormalized counter column.
--
-- 2026-05-20 FIX: this migration originally used
--   ALTER TABLE person ADD CONSTRAINT person_uuid_key UNIQUE USING INDEX idx__person__uuid;
-- but idx__person__uuid is a NON-UNIQUE index, so `USING INDEX` failed and
-- rolled back the entire transaction on every deploy — meaning the token
-- tables (token_ledger / revealed_likers / active_boosts) were NEVER
-- created. That in turn broke /search, which LEFT JOINs active_boosts, so
-- the discover deck was empty for every user. Rewritten to be idempotent
-- (the deploy re-runs every migration every time): add the unique
-- constraint directly (Postgres builds its own unique index) guarded by a
-- pg_constraint existence check, and CREATE ... IF NOT EXISTS / ADD COLUMN
-- IF NOT EXISTS throughout.

BEGIN;

-- person.uuid must be UNIQUE for the FKs below to reference it. The
-- original `USING INDEX idx__person__uuid` required that index to already
-- be unique, which it is not. Add the constraint directly instead, guarded
-- so re-runs are no-ops.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'person_uuid_key'
  ) THEN
    ALTER TABLE person ADD CONSTRAINT person_uuid_key UNIQUE (uuid);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS token_ledger (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  person_id   UUID NOT NULL REFERENCES person(uuid) ON DELETE CASCADE,
  delta       INTEGER NOT NULL CHECK (delta <> 0),
  reason      TEXT NOT NULL CHECK (reason IN (
                'purchase',
                'subscription_stipend',
                'reveal_liker',
                'super_like',
                'day_pass',
                'boost',
                'refund'
              )),
  metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS token_ledger_person_created_idx
  ON token_ledger (person_id, created_at DESC);

CREATE TABLE IF NOT EXISTS revealed_likers (
  viewer_id    UUID NOT NULL REFERENCES person(uuid) ON DELETE CASCADE,
  liker_id     UUID NOT NULL REFERENCES person(uuid) ON DELETE CASCADE,
  revealed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (viewer_id, liker_id)
);

CREATE TABLE IF NOT EXISTS active_boosts (
  person_id    UUID PRIMARY KEY REFERENCES person(uuid) ON DELETE CASCADE,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS active_boosts_expires_idx ON active_boosts (expires_at);

ALTER TABLE liked
  ADD COLUMN IF NOT EXISTS is_super BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
