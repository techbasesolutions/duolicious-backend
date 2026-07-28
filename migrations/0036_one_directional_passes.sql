-- Passes are one-directional (2026-07-29).
--
-- is_deck_suppressed (0035) hid a pair from EACH OTHER's decks on a
-- plain pass in either direction. On a small pool that has a real cost:
-- a new member passed on a searcher within minutes of joining, and the
-- searcher then never saw the new member at all ("the new user never
-- showed up in my feed"). The searcher never got the chance to like —
-- and a like is exactly what might change the other member's mind.
--
-- New rule:
--   * YOUR pass hides THEM from YOUR deck for 7 days (queue state).
--   * THEIR pass no longer hides them from YOUR deck.
--   * Reports/blocks stay strictly mutual and permanent.
--
-- Supersedes the is_deck_suppressed body in 0035. Idempotent:
-- CREATE OR REPLACE, safe on the re-run-every-deploy path.

CREATE OR REPLACE FUNCTION is_deck_suppressed(searcher INT, prospect INT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM skipped
        WHERE (
                -- The searcher's own pass or report.
                subject_person_id = searcher
                AND object_person_id = prospect
                AND (reported OR created_at > NOW() - INTERVAL '7 days')
          )
          OR (
                -- The prospect's action only counts when it is a report.
                subject_person_id = prospect
                AND object_person_id = searcher
                AND reported
          )
    );
$$;
