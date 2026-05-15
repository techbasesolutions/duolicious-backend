-- Phase W cutover — match loop tables.
--
-- The upstream Duolicious schema has /skip (pass) but no concept of
-- /like or mutual-match detection. Bumpy-style apps need both. This
-- migration adds:
--
--   liked        — append-only record of "user A liked user B"
--   match        — created when both directions of `liked` exist
--
-- The /decisions endpoint (POST /decisions) writes to `liked` on
-- decision="like" and computes the match via the unique constraint
-- below; on decision="nope" it delegates to the existing skip logic.

BEGIN;

CREATE TABLE IF NOT EXISTS liked (
    -- liker: the user pressing LIKE
    liker_id    INT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    -- liked: the user being liked
    liked_id    INT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (liker_id, liked_id),
    CHECK (liker_id <> liked_id)
);

CREATE INDEX IF NOT EXISTS idx__liked__liked_id ON liked (liked_id);

CREATE TABLE IF NOT EXISTS ahavah_match (
    -- Use a stable UUID for URL routing
    match_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- user_a_id is always the LOWER person.id; gives us a unique pair
    -- regardless of who liked first.
    user_a_id   INT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    user_b_id   INT NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (user_a_id < user_b_id),
    UNIQUE (user_a_id, user_b_id)
);

CREATE INDEX IF NOT EXISTS idx__ahavah_match__user_a ON ahavah_match (user_a_id);
CREATE INDEX IF NOT EXISTS idx__ahavah_match__user_b ON ahavah_match (user_b_id);

COMMIT;
