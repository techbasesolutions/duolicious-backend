-- 0014_token_economy.sql
-- Token economy: append-only ledger + reveal idempotency + boost TTL.
-- The ledger is the single source of truth for balance — compute via
-- SUM(delta) per person_id. No denormalized counter column.

BEGIN;

-- Plan deviation: existing `person` table has only an INDEX on `uuid`, not a
-- UNIQUE constraint, so we cannot FK to person(uuid) directly. Add the
-- unique constraint first so the FKs below are valid.
ALTER TABLE person
  ADD CONSTRAINT person_uuid_key UNIQUE USING INDEX idx__person__uuid;

CREATE TABLE token_ledger (
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
CREATE INDEX token_ledger_person_created_idx
  ON token_ledger (person_id, created_at DESC);

CREATE TABLE revealed_likers (
  viewer_id    UUID NOT NULL REFERENCES person(uuid) ON DELETE CASCADE,
  liker_id     UUID NOT NULL REFERENCES person(uuid) ON DELETE CASCADE,
  revealed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (viewer_id, liker_id)
);

CREATE TABLE active_boosts (
  person_id    UUID PRIMARY KEY REFERENCES person(uuid) ON DELETE CASCADE,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX active_boosts_expires_idx ON active_boosts (expires_at);

ALTER TABLE liked
  ADD COLUMN is_super BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
