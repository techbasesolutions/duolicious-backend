-- migrations/0040_community_unsubscribe.sql
-- E2 weekly community email gets its own unsubscribe category, separate
-- from `notifications` (match/like/message/verification mail): a member
-- can turn off the weekly digest replacement without silencing the mail
-- that tells them about matches. Idempotent.
ALTER TABLE person
  ADD COLUMN IF NOT EXISTS community_unsubscribed_at timestamptz;
