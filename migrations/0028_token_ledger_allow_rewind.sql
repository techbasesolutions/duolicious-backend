-- 0028_token_ledger_allow_rewind.sql
-- The rewind action (service/tokens/actions/rewind.py) debits token_ledger
-- with reason='rewind', but that value was never added to
-- token_ledger_reason_check. Every rewind therefore raised a CheckViolation
-- and returned 500 ("Couldn't rewind. Try again." on the client) -- the
-- feature has never once worked. Add 'rewind' to the allowed reasons.
-- Idempotent: drop + re-add the constraint with the full allowed set
-- (safe to re-run on every deploy; existing rows already comply).
BEGIN;

ALTER TABLE token_ledger DROP CONSTRAINT IF EXISTS token_ledger_reason_check;

ALTER TABLE token_ledger ADD CONSTRAINT token_ledger_reason_check
  CHECK (reason = ANY (ARRAY[
    'purchase',
    'subscription_stipend',
    'reveal_liker',
    'super_like',
    'day_pass',
    'boost',
    'refund',
    'referral',
    'rewind'
  ]::text[]));

COMMIT;
