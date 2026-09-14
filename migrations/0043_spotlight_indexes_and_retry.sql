-- migrations/0043_spotlight_indexes_and_retry.sql
--
-- Two things the queue needs once real traffic arrives.
--
-- 1. campaign_link is scanned by kind on every queue listing (the clicks and
--    sign-ups subqueries in service/api/admin/spotlight_routes.py match
--    'post:' || request_key) and by service.growth.queries.post_stats. With
--    one row per published card that grows for as long as the feature runs,
--    so kind gets its own index.
--
-- 2. claim_spotlight_posts re-issued (same body as 0041) with the selection
--    widened: a transient failure now retries on its own. The publisher marks
--    a row 'failed' only when nothing was sent to the platform (an attempted
--    call whose response was lost becomes 'review' instead, never 'failed'),
--    so re-claiming a failed row cannot double post. attempts is capped at 3
--    by the same MAX_ATTEMPTS the manual retry path enforces, and a row that
--    reaches it stays failed for a human to look at.

CREATE INDEX IF NOT EXISTS campaign_link_kind_idx ON campaign_link (kind);

CREATE OR REPLACE FUNCTION claim_spotlight_posts(max_rows int)
RETURNS SETOF publishing_queue
LANGUAGE sql
AS $$
  UPDATE publishing_queue q
     SET status = 'processing',
         lease_until = NOW() + interval '10 minutes',
         attempts = attempts + 1,
         error = NULL,
         updated_at = NOW()
   WHERE q.id IN (
     SELECT id FROM publishing_queue
      WHERE scheduled_for <= NOW()
        AND (status = 'scheduled'
             OR (status = 'failed' AND attempts < 3))
      ORDER BY scheduled_for
      FOR UPDATE SKIP LOCKED
      LIMIT max_rows)
  RETURNING q.*;
$$;
