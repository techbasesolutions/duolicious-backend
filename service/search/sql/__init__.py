"""
service.search.sql — SQL fragments for the search service.

Task 0.3e (Q&A subsystem strip per audit) replaced the Q&A-driven SQL with
minimal stubs. The upstream Duolicious fork's SQL relied on a `personality` vector
column, `count_answers`, and JOINs against the `answer` /
`search_preference_answer` tables — all of which are dropped in Task 0.3d.

Phase 1 Task 1.1 will rewrite this with:
  - country / region / languages_spoken filters
  - composite index `(country, verification_level DESC, last_active DESC)`
  - swipe-exclusion JOIN against the `swipe` table
  - block exclusion against the `hide-and-block` table

Until Phase 1, the stubs below return an empty/minimal result so that:
  - The /search HTTP route doesn't 500
  - service.person callers (which `from service.search.sql import *`)
    don't ImportError
  - The frontend renders an empty Search/Discover tab gracefully
"""

from commonsql import Q_COMPUTED_FLAIR  # noqa: F401  # re-exported via `from service.search.sql import *` (consumed by service/person/__init__.py)

# --- Search-preference fragments (kept; not Q&A-related) -------------------

Q_UPSERT_SEARCH_PREFERENCE_CLUB = """
SELECT 1
"""

Q_SEARCH_PREFERENCE = f"""
SELECT
    COALESCE(
        ARRAY(
            SELECT gender_id FROM search_preference_gender
            WHERE person_id = %(person_id)s
        ),
        ARRAY[]::SMALLINT[]
    ) AS gender_id_array,
    1 AS gender_id   -- legacy column expected by callers; first row is consumed
WHERE FALSE
UNION ALL
SELECT
    ARRAY[]::SMALLINT[],
    gender_id
FROM search_preference_gender
WHERE person_id = %(person_id)s
"""

# --- Search result fragments — stubbed pending Phase 1 ---------------------

Q_UNCACHED_SEARCH_1 = """
DELETE FROM search_cache WHERE searcher_person_id = %(searcher_person_id)s
"""

# Phase 1 Task 1.1 — discovery query.
#   - filters: country (if user set preferred_countries), language overlap
#     (if user set preferred_languages), gender preference, activated
#   - excludes: self, already-swiped (any direction), blocked (skipped table)
#   - sorted by: verification_level DESC, last_online_time DESC
#     (verified-first, recently-active; matches the composite index)
#   - paginated via n / o
#
# When `preferred_countries` is empty, country filter is skipped entirely
# (user is open to all countries). Same for languages. This lets new accounts
# without explicit prefs see the global pool.
Q_UNCACHED_SEARCH_2 = """
WITH searcher_prefs AS (
    SELECT
        ARRAY(
            SELECT country FROM search_preference_country
            WHERE person_id = %(searcher_person_id)s
        ) AS preferred_countries,
        ARRAY(
            SELECT language FROM search_preference_language
            WHERE person_id = %(searcher_person_id)s
        ) AS preferred_languages,
        COALESCE(
            (SELECT open_to_long_distance FROM search_preference_open_to_long_distance
             WHERE person_id = %(searcher_person_id)s),
            TRUE
        ) AS open_to_long_distance
),
prospect_pool AS (
    SELECT
        p.id,
        p.uuid AS uuid_raw,
        p.uuid::text AS uuid,
        p.name,
        EXTRACT(YEAR FROM AGE(p.date_of_birth))::int AS age,
        '[]'::jsonb AS photos,
        (p.verification_level_id > 1) AS verified,
        p.location_short_friendly AS location,
        0 AS match_percentage,
        FALSE AS is_skipped,
        FALSE AS is_messaged,
        ''::text AS gender,
        ''::text AS sexual_orientation,
        p.verification_level_id,
        p.last_online_time
    FROM person p
    CROSS JOIN searcher_prefs sp
    WHERE p.activated = TRUE
      AND p.id != %(searcher_person_id)s
      AND p.gender_id = ANY(%(gender_preference)s::SMALLINT[])

      -- Country filter: applied only if user has expressed preferences
      AND (
          cardinality(sp.preferred_countries) = 0
          OR p.country = ANY(sp.preferred_countries)
      )

      -- Language overlap: applied only if user has expressed preferences
      AND (
          cardinality(sp.preferred_languages) = 0
          OR p.languages_spoken && sp.preferred_languages
      )

      -- Already-swiped exclusion (any direction)
      AND NOT EXISTS (
          SELECT 1 FROM swipe s
          WHERE s.swiper_person_id = %(searcher_person_id)s
            AND s.swiped_person_id = p.id
      )

      -- Blocked exclusion (the upstream Duolicious fork's existing skipped table — both directions)
      AND NOT EXISTS (
          SELECT 1 FROM skipped sk
          WHERE (sk.subject_person_id = %(searcher_person_id)s AND sk.object_person_id = p.id)
             OR (sk.subject_person_id = p.id AND sk.object_person_id = %(searcher_person_id)s)
      )
)
INSERT INTO search_cache (
    searcher_person_id, position, prospect_person_id, prospect_uuid,
    profile_photo_uuid, name, match_percentage, personality
)
SELECT
    %(searcher_person_id)s,
    (ROW_NUMBER() OVER (ORDER BY p.verification_level_id DESC, p.last_online_time DESC, p.id) - 1)::SMALLINT,
    p.id,
    p.uuid_raw,
    NULL,
    p.name,
    0,
    -- Q&A subsystem was stripped (Task 0.3e); search_cache.personality is
    -- still declared NOT NULL VECTOR(47) by the upstream fork schema. Insert
    -- a zero vector so the constraint is satisfied; nothing reads this column
    -- post-strip (Q_CACHED_SEARCH no longer references it).
    ('[' || array_to_string(array_fill(0, ARRAY[47]), ',') || ']')::vector
FROM prospect_pool p
LIMIT 1000
"""

Q_CACHED_SEARCH = """
SELECT
    p.id AS prospect_person_id,
    p.uuid::text AS prospect_uuid,
    p.name,
    EXTRACT(YEAR FROM AGE(p.date_of_birth))::int AS age,
    -- photo_uuids: ordered JSON array of photo UUIDs (position ASC). The
    -- frontend's use-discover-deck adapter maps each entry through
    -- cdnUrlFor() to build the CDN URL. Empty array `[]` when the
    -- prospect has not uploaded any photos yet (e.g. signed up via OTP
    -- but bailed before /onboarding/photos).
    COALESCE(
        (
            SELECT json_agg(ph.uuid ORDER BY ph.position)
            FROM photo ph
            WHERE ph.person_id = p.id
        ),
        '[]'::json
    )::jsonb AS photo_uuids,
    -- profile_photo_uuid: the position=1 photo (or first by position if
    -- 1 is empty). Kept for backwards-compat with any consumer that
    -- expects the single-photo wire field; the carousel uses photo_uuids.
    (
        SELECT ph.uuid
        FROM photo ph
        WHERE ph.person_id = p.id
        ORDER BY ph.position
        LIMIT 1
    ) AS profile_photo_uuid,
    0 AS match_percentage,
    NULL::text AS verification_required,
    p.location_short_friendly AS location,
    p.country AS country,
    -- Drives the green-dot / "last seen Xm ago" affordance on /discover,
    -- /matches, and the chat header. NULL when the prospect has never
    -- been signed in (fresh seed account, etc.) — frontend treats NULL
    -- as "no signal", neither online nor a stamped time.
    EXTRACT(EPOCH FROM NOW() - p.last_online_time)::int AS seconds_since_last_online,
    -- Map opt-out: prospect set "Show me on the map" → off in privacy
    -- settings (stored as ahavah_extra.showOnMap = false). Default TRUE
    -- when the key is absent (legacy users + new accounts). Frontend
    -- /map filters markers on this; /discover ignores it (the same row
    -- is allowed to appear in the swipe deck).
    COALESCE((p.ahavah_extra->>'showOnMap')::boolean, TRUE) AS show_on_map
FROM search_cache sc
JOIN person p ON p.id = sc.prospect_person_id
WHERE sc.searcher_person_id = %(searcher_person_id)s
ORDER BY sc.position
LIMIT %(n)s OFFSET %(o)s
"""

# Q_QUIZ_SEARCH removed in Task 0.3e — it was the Q&A-scored "first result"
# query. With Q&A gone, the same answer is "the top of Q_CACHED_SEARCH" so
# callers that previously asked for quiz-search now use uncached-search.

# --- Feed query (NOT Q&A-related; kept intact) -----------------------------

Q_FEED = """
SELECT '{}'::jsonb AS j WHERE FALSE
"""
# Q_FEED stub: the original feed query referenced the answer table and
# `personality`-derived "people you might like" suggestions. Phase 1 (or
# later) will rebuild the feed with country/language-based recommendations.
