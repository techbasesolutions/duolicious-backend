-- Phase W cutover — Stripe Checkout webhook needs a person ↔ Stripe
-- customer mapping. The first event for a paid user is
-- `checkout.session.completed`, which carries our
-- `metadata.user_id` (set at session-create time in
-- service/checkout/__init__.py). All later events for the same
-- subscription (customer.subscription.updated, .deleted, invoice.*)
-- only carry `customer` (= the Stripe Customer ID), so we need a
-- DB-side lookup or we'll fail to revoke premium on cancel.
--
-- Storing it on person keeps the lookup O(1) without a separate
-- subscriptions table for now. Phase 5 IAP (RevenueCat) flow stays
-- unaffected — that path doesn't use this column.

BEGIN;

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

CREATE INDEX IF NOT EXISTS idx__person__stripe_customer_id
  ON person(stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;

COMMIT;
