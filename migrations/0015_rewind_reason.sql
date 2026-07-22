-- 0015_rewind_reason.sql
-- Discover Rewind (2026-05-19): the token_ledger.reason CHECK from 0014
-- does not include 'rewind', so the rewind spend can't append its debit
-- row. Extend the CHECK to the superset. Idempotent (the deploy re-runs
-- every migration every time): drop the existing CHECK by its conventional
-- name, then re-add the superset guarded by a pg_constraint existence
-- check. Verified 2026-05-19 on the droplet that the constraint is named
-- token_ledger_reason_check.

BEGIN;

ALTER TABLE token_ledger DROP CONSTRAINT IF EXISTS token_ledger_reason_check;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'token_ledger_reason_check'
  ) THEN
    ALTER TABLE token_ledger
      ADD CONSTRAINT token_ledger_reason_check CHECK (reason IN (
        'purchase',
        'subscription_stipend',
        'reveal_liker',
        'super_like',
        'day_pass',
        'boost',
        'refund',
        'rewind',
        -- 'referral' arrived later (0024) but this migration RE-RUNS on
        -- every deploy: it drops the constraint unconditionally, so
        -- omitting a reason already present in the data made the re-add
        -- fail every single time ("check constraint is violated by some
        -- row", 2026-07-22). The transaction rolled back so no damage,
        -- but the noise would mask a migration that genuinely broke.
        -- Keep this list a superset of every reason ever allowed.
        'referral'
      ));
  END IF;
END $$;

COMMIT;
