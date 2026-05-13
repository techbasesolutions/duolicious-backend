-- Phase 1 Task 1.1 — first Ahavah-specific migration.
--
-- Adds:
--   - country / region / languages_spoken / primary_language / auto_translate_enabled
--     columns on `person` (the international-discovery axis)
--   - composite index tuned for the Ahavah search sort
--     (verification_level DESC, last_online_time DESC, optional country filter)
--   - GIN index on `languages_spoken` for ANY-overlap queries
--   - `swipe` table (left/right/super) — net-new; the existing `skipped` table
--     stays as duolicious left it (hide-and-block surface uses it)
--
-- Q&A table drops (was Task 0.3d) are deliberately NOT in this migration —
-- service/person/sql/__init__.py + antiabuse/sql/__init__.py still hold SQL
-- string constants referencing answer/question/trait/personality. Those
-- constants aren't currently executed (the methods that called them were
-- removed in Task 0.3f), so leaving the tables avoids setting a runtime
-- trap. They get dropped in a later cleanup migration once the SQL strings
-- have been audited.

BEGIN;

-- 1. Geo + language columns -----------------------------------------------

ALTER TABLE person
  ADD COLUMN IF NOT EXISTS country CHAR(2),                                          -- ISO-3166-1 alpha-2
  ADD COLUMN IF NOT EXISTS region TEXT,                                              -- free-text state/province
  ADD COLUMN IF NOT EXISTS languages_spoken TEXT[] NOT NULL DEFAULT '{}',           -- ISO-639-1 codes
  ADD COLUMN IF NOT EXISTS primary_language TEXT NOT NULL DEFAULT 'EN-US',           -- DeepL target lang
  ADD COLUMN IF NOT EXISTS auto_translate_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- 2. Indexes for the new discovery sort -----------------------------------

-- Plain index on country for filter queries
CREATE INDEX IF NOT EXISTS idx_person_country
  ON person(country)
  WHERE activated = TRUE;

-- Composite for sort: country filter + verified-first + recently-active
CREATE INDEX IF NOT EXISTS idx_person_discovery_sort
  ON person(country, verification_level_id DESC, last_online_time DESC)
  WHERE activated = TRUE;

-- GIN for languages_spoken overlap queries
CREATE INDEX IF NOT EXISTS idx_person_languages_spoken
  ON person USING GIN (languages_spoken);

-- 3. Search-preference extensions -----------------------------------------

-- Existing duolicious search_preference_* tables stay; we add Ahavah-specific
-- discovery prefs as additional optional rows.
CREATE TABLE IF NOT EXISTS search_preference_country (
  person_id INT NOT NULL REFERENCES person(id) ON DELETE CASCADE ON UPDATE CASCADE,
  country CHAR(2) NOT NULL,
  PRIMARY KEY (person_id, country)
);

CREATE TABLE IF NOT EXISTS search_preference_language (
  person_id INT NOT NULL REFERENCES person(id) ON DELETE CASCADE ON UPDATE CASCADE,
  language TEXT NOT NULL,
  PRIMARY KEY (person_id, language)
);

CREATE TABLE IF NOT EXISTS search_preference_open_to_long_distance (
  person_id INT PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE ON UPDATE CASCADE,
  open_to_long_distance BOOLEAN NOT NULL DEFAULT TRUE
);

-- 4. Swipe table (net-new — distinct from duolicious's `skipped`) ---------

DO $$ BEGIN
  CREATE TYPE swipe_direction AS ENUM ('like', 'pass', 'super');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS swipe (
  swiper_person_id INT  NOT NULL REFERENCES person(id) ON DELETE CASCADE ON UPDATE CASCADE,
  swiped_person_id INT  NOT NULL REFERENCES person(id) ON DELETE CASCADE ON UPDATE CASCADE,
  direction        swipe_direction NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (swiper_person_id, swiped_person_id),
  CHECK (swiper_person_id != swiped_person_id)
);

CREATE INDEX IF NOT EXISTS idx_swipe_swiper_recent
  ON swipe(swiper_person_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_swipe_mutual_like
  ON swipe(swiped_person_id, swiper_person_id)
  WHERE direction IN ('like', 'super');

COMMIT;
