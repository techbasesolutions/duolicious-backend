-- migrations/0048_click_receipts.sql
-- F10: a click now mints its own opaque receipt. The campaign key stays what
-- it always was, a shared identifier for the post, which is exactly why it
-- cannot be the thing that proves a particular visitor clicked.
--
-- Rows written before this migration keep a null receipt and are therefore
-- not creditable under the new scheme. That is deliberate, not an
-- oversight: nothing proves who made them.
--
-- Idempotent.
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS receipt     text;
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS platform    text;
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS consumed_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS campaign_click_receipt_idx
    ON campaign_click (receipt) WHERE receipt IS NOT NULL;
