-- Root fix for a recurring bug class (2026-07-22).
--
-- `skipped` stores TWO different things, told apart only by the
-- `reported` boolean:
--
--   * a PASS  — soft, transient deck state ("not for me right now")
--   * a BLOCK — a hard, permanent safety barrier (report/block)
--
-- That is inherited from the upstream fork, where "skip" meant something
-- close to block. Ahavah layered a Tinder-style pass onto the same table
-- without separating the concepts, so every query that touches `skipped`
-- has to REMEMBER to check `reported`. Any query that forgets silently
-- promotes a casual swipe into a permanent ban.
--
-- The cost of that was four different interpretations of one table:
--   deck    : reported OR younger than 7 days   (correct)
--   map     : reported only                     (fixed 2026-07-21)
--   profile : ANY skip -> permanent 404         (fixed 2026-07-22)
--   likes / conversation / chat gate: ANY skip  (fixed here)
--
-- Symptoms members actually reported: an empty world map, both Views
-- tabs blank for an active swiper, and 6 of 13 profiles refusing to
-- open. Same root every time.
--
-- These two functions give each meaning a NAME, so a caller has to pick
-- one deliberately instead of hand-rolling a predicate and forgetting a
-- clause. Every visibility gate now calls one of them.
--
-- Idempotent: CREATE OR REPLACE, safe on the re-run-every-deploy path.

-- The hard barrier. Symmetric: a block hides in both directions, for
-- every surface (profile, views, map, likes, conversations, chat).
CREATE OR REPLACE FUNCTION is_blocked_pair(a INT, b INT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM skipped
        WHERE reported
          AND (
                (subject_person_id = a AND object_person_id = b)
             OR (subject_person_id = b AND object_person_id = a)
          )
    );
$$;

-- Deck-only suppression. A pass takes someone out of YOUR queue for 7
-- days; a block is permanent. This is the ONLY place a plain pass may
-- hide anyone — it is queue state, not a visibility rule.
CREATE OR REPLACE FUNCTION is_deck_suppressed(searcher INT, prospect INT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM skipped
        WHERE (
                (subject_person_id = searcher AND object_person_id = prospect)
             OR (subject_person_id = prospect AND object_person_id = searcher)
          )
          AND (reported OR created_at > NOW() - INTERVAL '7 days')
    );
$$;
