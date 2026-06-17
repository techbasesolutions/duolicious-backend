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
        ) AS open_to_long_distance,
        -- Searcher's own location intent + point, for the distance gate below.
        (SELECT ahavah_extra->'intent' FROM person
          WHERE id = %(searcher_person_id)s) AS searcher_intent,
        (SELECT coordinates FROM person
          WHERE id = %(searcher_person_id)s) AS searcher_coords
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
        p.last_online_time,
        (ab.person_id IS NOT NULL) AS is_boosted
    FROM person p
    CROSS JOIN searcher_prefs sp
    -- Phase 7 Task 7.2 — surface boosted candidates first in /search.
    -- active_boosts is upserted by service.tokens.actions.boost.perform()
    -- with a 30-minute TTL. LEFT JOIN so non-boosted prospects still
    -- appear; the (ab.person_id IS NOT NULL) sort key (below) ranks
    -- boosted rows ahead of everyone else within the existing ordering.
    LEFT JOIN active_boosts ab
           ON ab.person_id = p.uuid
          AND ab.expires_at > NOW()
    WHERE p.activated = TRUE
      AND p.id != %(searcher_person_id)s
      AND p.gender_id = ANY(%(gender_preference)s::SMALLINT[])

      -- Country filter: applied only if user has expressed preferences
      AND (
          cardinality(sp.preferred_countries) = 0
          OR p.country = ANY(sp.preferred_countries)
      )

      -- Distance gate (functional local-only / open-to-relocation): when the
      -- searcher's intent includes "local-only", restrict the pool to
      -- prospects within %(local_radius_m)s metres. "open-to-relocation"
      -- overrides it (distance never blocks), and an absent/legacy intent or
      -- a missing point on either side also means no cap (fail open — never
      -- silently empty the deck).
      AND (
          sp.searcher_intent IS NULL
          OR NOT (sp.searcher_intent ? 'local-only')
          OR (sp.searcher_intent ? 'open-to-relocation')
          OR sp.searcher_coords IS NULL
          OR p.coordinates IS NULL
          OR ST_DWithin(sp.searcher_coords, p.coordinates, %(local_radius_m)s)
      )

      -- Language overlap: applied only if user has expressed preferences
      AND (
          cardinality(sp.preferred_languages) = 0
          OR p.languages_spoken && sp.preferred_languages
      )

      -- "Hide me from strangers" privacy toggle. The toggle is also
      -- enforced on Q_SELECT_PROSPECT_PROFILE (the profile-detail gate
      -- lets messaged peers through), but /search always runs from a
      -- stranger viewpoint — by definition the searcher hasn't messaged
      -- anyone in the discover pool yet, so any TRUE here means exclude.
      AND NOT p.hide_me_from_strangers

      -- Already-swiped exclusion (any direction)
      AND NOT EXISTS (
          SELECT 1 FROM swipe s
          WHERE s.swiper_person_id = %(searcher_person_id)s
            AND s.swiped_person_id = p.id
      )

      -- Already-liked exclusion. The like flow (service/decisions:Q_RECORD_LIKE)
      -- writes ONLY into `liked`, not `swipe` — so a liked candidate would
      -- reappear on the next deck refresh unless explicitly filtered here.
      -- Reported: same person keeps surfacing after a like/unlike + reload.
      AND NOT EXISTS (
          SELECT 1 FROM liked l
          WHERE l.liker_id = %(searcher_person_id)s
            AND l.liked_id = p.id
      )

      -- Blocked exclusion (the upstream Duolicious fork's existing skipped table — both directions)
      AND NOT EXISTS (
          SELECT 1 FROM skipped sk
          WHERE (sk.subject_person_id = %(searcher_person_id)s AND sk.object_person_id = p.id)
             OR (sk.subject_person_id = p.id AND sk.object_person_id = %(searcher_person_id)s)
      )

      -- Phase W: "Verified only" filter. Triggered either by the
      -- discover sheet's verifiedOnly toggle OR the privacy setting
      -- "Require my matches to be verified". Either path passes
      -- verified_only=TRUE to the search service, and the backend
      -- excludes prospects who are at 'none' on the new tier ENUM AND
      -- below 'Photos' on the legacy lookup. Verified = Bronze or
      -- better (any tier ladder entry that proves identity).
      AND (
          NOT %(verified_only)s::boolean
          OR p.ahavah_verification_tier <> 'none'::ahavah_verification_tier
          OR p.verification_level_id > 1
      )

      -- Phase W cutover (2026-05-15) — pill-grid filters from the
      -- discover/map FiltersSheet. Each is a comma-joined list of
      -- kebab-case enum values; we filter against
      -- p.ahavah_extra->>'<field>' so the precise Torah-observant
      -- values round-trip (the upstream Duolicious enum tables are
      -- coarse and lossy — e.g. re-married collapses to Married, and
      -- the assembly/torahLevel/polygyny/calendar fields have no
      -- first-class column at all). Empty array = no filter; the
      -- cardinality()=0 guard skips the ANY() check entirely so the
      -- normal pool isn't narrowed.

      AND (
          cardinality(%(intents)s::TEXT[]) = 0
          -- intent is a multi-value array (ahavah_extra.intent). Match when the
          -- prospect's array overlaps any selected filter value. The old
          -- ->>'intent' = ANY(...) scalar test silently stopped matching once
          -- intent became an array.
          OR p.ahavah_extra->'intent' ?| %(intents)s::TEXT[]
      )
      AND (
          cardinality(%(marital_statuses)s::TEXT[]) = 0
          OR p.ahavah_extra->>'maritalStatus' = ANY(%(marital_statuses)s::TEXT[])
      )
      -- Children: 2-bucket filter ("has" / "none"). The frontend
      -- multi-select can pass both buckets (meaning "any value
      -- present") or just one. Backend treats both selected as a
      -- no-op (every candidate matches one or the other).
      AND (
          cardinality(%(has_children_buckets)s::TEXT[]) = 0
          OR (
              'has' = ANY(%(has_children_buckets)s::TEXT[])
              AND COALESCE((p.ahavah_extra->>'children')::INT, 0) > 0
          )
          OR (
              'none' = ANY(%(has_children_buckets)s::TEXT[])
              AND COALESCE((p.ahavah_extra->>'children')::INT, 0) = 0
          )
      )
      AND (
          cardinality(%(assemblies)s::TEXT[]) = 0
          -- assembly is a multi-value array (ahavah_extra.assembly). Match on
          -- array overlap, same scalar-vs-array fix as the intent filter above.
          OR p.ahavah_extra->'assembly' ?| %(assemblies)s::TEXT[]
      )
      AND (
          cardinality(%(torah_levels)s::TEXT[]) = 0
          OR p.ahavah_extra->>'torahLevel' = ANY(%(torah_levels)s::TEXT[])
      )
      AND (
          cardinality(%(polygyny_stances)s::TEXT[]) = 0
          OR p.ahavah_extra->>'polygyny' = ANY(%(polygyny_stances)s::TEXT[])
      )
      AND (
          cardinality(%(calendars)s::TEXT[]) = 0
          OR p.ahavah_extra->>'calendar' = ANY(%(calendars)s::TEXT[])
      )
      AND (
          cardinality(%(educations)s::TEXT[]) = 0
          OR p.ahavah_extra->>'education' = ANY(%(educations)s::TEXT[])
      )
      -- Health tags: prospect.healthTags JSON array must include every
      -- selected tag (AND semantics — picking "non-smoker" + "fitness"
      -- requires both). JSONB containment via `?&` operator.
      AND (
          cardinality(%(health_tags)s::TEXT[]) = 0
          OR (
              p.ahavah_extra->'healthTags' IS NOT NULL
              AND p.ahavah_extra->'healthTags' ?& %(health_tags)s::TEXT[]
          )
      )

      -- Age filter (ephemeral, body-driven from FiltersSheet age
      -- slider). Bounds are inclusive. NULL on either side = open-ended
      -- (so a one-sided slider drag still works). Whole-year resolution
      -- via EXTRACT(YEAR FROM AGE(date_of_birth)) — same expression as
      -- the SELECT clause that computes `age` for the response, keeping
      -- "what the user filtered on" and "what the user is shown"
      -- consistent.
      AND (
          %(age_min)s::INT IS NULL
          OR EXTRACT(YEAR FROM AGE(p.date_of_birth))::INT >= %(age_min)s::INT
      )
      AND (
          %(age_max)s::INT IS NULL
          OR EXTRACT(YEAR FROM AGE(p.date_of_birth))::INT <= %(age_max)s::INT
      )
)
INSERT INTO search_cache (
    searcher_person_id, position, prospect_person_id, prospect_uuid,
    profile_photo_uuid, name, match_percentage, personality
)
SELECT
    %(searcher_person_id)s,
    (ROW_NUMBER() OVER (ORDER BY p.is_boosted DESC, p.verification_level_id DESC, p.last_online_time DESC, p.id) - 1)::SMALLINT,
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
    -- Conditional on p.show_my_age — same gate as profile detail so the
    -- discover deck card and the underlying profile agree.
    (SELECT EXTRACT(YEAR FROM AGE(p.date_of_birth))::int WHERE p.show_my_age) AS age,
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
    -- Conditional on p.show_my_location — same gate as profile detail.
    (SELECT p.location_short_friendly WHERE p.show_my_location) AS location,
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
    COALESCE((p.ahavah_extra->>'showOnMap')::boolean, TRUE) AS show_on_map,
    -- Precise map position (city-level) from person.coordinates. NULL when the
    -- prospect has no stored point; the FE map-avatar then falls back to the
    -- country centroid. GEOGRAPHY(Point,4326) -> geometry cast for ST_X/ST_Y.
    ST_Y(p.coordinates::geometry) AS latitude,
    ST_X(p.coordinates::geometry) AS longitude
FROM search_cache sc
JOIN person p ON p.id = sc.prospect_person_id
WHERE sc.searcher_person_id = %(searcher_person_id)s
ORDER BY sc.position
LIMIT %(n)s OFFSET %(o)s
"""

# Q_MAP_MARKERS — every map marker for the current viewer, as individual
# points. READ-ONLY: reads the viewer's existing search_cache (the full
# filtered match set Q_BUILD already inserted, up to LIMIT 1000); it never
# rebuilds the cache. One row per match with show_my_location + showOnMap on.
# The frontend renders each as an avatar pin and lets Leaflet's client-side
# MarkerClusterGroup cluster + spiderfy them (so same-coordinate users can be
# fanned apart). Fetched once per filter set, not per pan.
Q_MAP_MARKERS = """
    SELECT
        p.uuid::text AS uuid,
        p.name,
        p.country,
        ST_Y(p.coordinates::geometry) AS lat,
        ST_X(p.coordinates::geometry) AS lng,
        (
            SELECT ph.uuid FROM photo ph
            WHERE ph.person_id = p.id
            ORDER BY ph.position
            LIMIT 1
        ) AS photo_uuid
    FROM search_cache sc
    JOIN person p ON p.id = sc.prospect_person_id
    WHERE sc.searcher_person_id = %(searcher_person_id)s
      AND p.show_my_location
      AND COALESCE((p.ahavah_extra->>'showOnMap')::boolean, TRUE)
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
