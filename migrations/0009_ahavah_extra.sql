-- 0009: Persist Ahavah-specific (Torah-observant) profile fields.
--
-- Duolicious's schema models a generic dating-app shape — gender,
-- relationship status, looking-for — but has no columns for the
-- Torah-observant profile fields Ahavah captures during onboarding
-- (assembly, torah-level, shabbat, feast days, polygyny view, head
-- covering, tzitzit, calendar, family views, living preferences,
-- health tags, interests, personality traits, relocation, intent).
--
-- Storing these per-field would require ~15 new columns + lookup
-- tables for the enums. A single JSONB blob is the pragmatic shape:
-- the frontend already validates the values via TypeScript enums in
-- profile-schema.ts; the backend just needs to round-trip the blob.
--
-- Mirrored on the onboardee table so the wizard can write before the
-- person row exists; /finish-onboarding copies onboardee.ahavah_extra
-- into the new person row's column (handled in service/person/sql).

ALTER TABLE person
    ADD COLUMN IF NOT EXISTS ahavah_extra JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE onboardee
    ADD COLUMN IF NOT EXISTS ahavah_extra JSONB NOT NULL DEFAULT '{}'::jsonb;

-- No index — these fields are read-by-id only, never queried/filtered
-- server-side. Filtering (e.g. "polygyny: monogamy only") is done in
-- frontend after the search result list comes back.
