"""
service.decisions — Phase W match-loop endpoints.

Provides:

    POST   /decisions          — record a like or pass; on mutual-like, create match
    GET    /matches            — list mutual matches for the session user
    GET    /match/<match_id>   — one match record + the matched peer's profile

The upstream Duolicious schema doesn't model mutual matching as a
first-class concept (it has /skip and emergent inbox conversations).
Phase W layers the explicit Bumpy-style like-and-match flow on top of
the existing person/photo schema via the `liked` + `ahavah_match`
tables (see migrations/0006_match_loop.sql).
"""

from typing import Optional
from database import api_tx
import duotypes as t


Q_RECORD_LIKE = """
WITH new_like AS (
    INSERT INTO liked (liker_id, liked_id)
    SELECT
        %(me_id)s,
        person.id
    FROM
        person
    WHERE
        person.uuid = uuid_or_null(%(prospect_uuid)s)
    AND
        person.id <> %(me_id)s
    ON CONFLICT DO NOTHING
    RETURNING liker_id, liked_id
), reciprocal AS (
    SELECT 1 AS exists_reciprocal
    FROM new_like nl
    JOIN liked rev
      ON rev.liker_id = nl.liked_id
     AND rev.liked_id = nl.liker_id
), inserted_match AS (
    INSERT INTO ahavah_match (user_a_id, user_b_id)
    SELECT
        LEAST(nl.liker_id, nl.liked_id),
        GREATEST(nl.liker_id, nl.liked_id)
    FROM new_like nl
    JOIN reciprocal r ON TRUE
    ON CONFLICT (user_a_id, user_b_id) DO NOTHING
    RETURNING match_id, user_a_id, user_b_id
)
SELECT
    m.match_id::text AS match_id,
    CASE
        WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
        ELSE m.user_a_id
    END AS peer_id,
    peer.uuid::text AS peer_uuid
FROM inserted_match m
JOIN person peer
  ON peer.id = CASE
        WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
        ELSE m.user_a_id
    END
"""


Q_LIST_MATCHES = """
SELECT
    m.match_id::text AS match_id,
    peer.uuid::text  AS peer_uuid,
    peer.name        AS peer_name,
    EXTRACT(YEAR FROM AGE(peer.date_of_birth))::int AS peer_age,
    -- Position-ordered JSON array of peer photo UUIDs. Frontend maps each
    -- through cdnUrlFor() so /matches + /match show the peer's face
    -- instead of falling through to the gradient stamp.
    COALESCE(
        (
            SELECT json_agg(ph.uuid ORDER BY ph.position)
            FROM photo ph
            WHERE ph.person_id = peer.id
        ),
        '[]'::json
    )::jsonb AS peer_photo_uuids,
    m.created_at::text AS created_at
FROM
    ahavah_match m
JOIN person peer
  ON peer.id = CASE
        WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
        ELSE m.user_a_id
    END
WHERE
    m.user_a_id = %(me_id)s OR m.user_b_id = %(me_id)s
ORDER BY
    m.created_at DESC
"""


Q_LIST_INCOMING_LIKES = """
-- People who liked the session user but for whom the session user has
-- NOT yet decided (no reverse like, no skip / report).
-- Powers /matches "Liked you" tab.
SELECT
    liker.uuid::text AS liker_uuid,
    liker.name       AS liker_name,
    EXTRACT(YEAR FROM AGE(liker.date_of_birth))::int AS liker_age,
    COALESCE(
        (
            SELECT json_agg(ph.uuid ORDER BY ph.position)
            FROM photo ph
            WHERE ph.person_id = liker.id
        ),
        '[]'::json
    )::jsonb AS liker_photo_uuids,
    l.created_at::text AS created_at
FROM
    liked l
JOIN person liker ON liker.id = l.liker_id
WHERE
    l.liked_id = %(me_id)s
    -- Exclude prospects we've already liked (those become matches via
    -- /matches, not "Liked you")
    AND NOT EXISTS (
        SELECT 1 FROM liked rev
        WHERE rev.liker_id = %(me_id)s
          AND rev.liked_id = l.liker_id
    )
    -- Exclude anyone we've skipped / reported in either direction.
    AND NOT EXISTS (
        SELECT 1 FROM skipped s
        WHERE (s.subject_person_id = %(me_id)s AND s.object_person_id = l.liker_id)
           OR (s.subject_person_id = l.liker_id AND s.object_person_id = %(me_id)s)
    )
    -- Liker must still be activated (no soft-deleted accounts).
    AND liker.activated = TRUE
    -- Defensive: skip anyone we already share a confirmed match with
    -- (covers the case where one half of `liked` was wiped but the
    -- match row survives — that user belongs in 'Matches', not here).
    AND NOT EXISTS (
        SELECT 1 FROM ahavah_match m
        WHERE (m.user_a_id = %(me_id)s AND m.user_b_id = l.liker_id)
           OR (m.user_b_id = %(me_id)s AND m.user_a_id = l.liker_id)
    )
ORDER BY
    l.created_at DESC
LIMIT 200
"""


Q_GET_MATCH = """
SELECT
    m.match_id::text   AS match_id,
    peer.uuid::text    AS peer_uuid,
    peer.name          AS peer_name,
    EXTRACT(YEAR FROM AGE(peer.date_of_birth))::int AS peer_age,
    COALESCE(
        (
            SELECT json_agg(ph.uuid ORDER BY ph.position)
            FROM photo ph
            WHERE ph.person_id = peer.id
        ),
        '[]'::json
    )::jsonb AS peer_photo_uuids,
    m.created_at::text AS created_at
FROM
    ahavah_match m
JOIN person peer
  ON peer.id = CASE
        WHEN m.user_a_id = %(me_id)s THEN m.user_b_id
        ELSE m.user_a_id
    END
WHERE
    m.match_id = uuid_or_null(%(match_id)s)
AND
    (m.user_a_id = %(me_id)s OR m.user_b_id = %(me_id)s)
"""


def post_decisions(req: t.PostDecision, s: t.SessionInfo):
    """
    Record a like or pass on the given prospect.

    Body:
        { "profile_uuid": "<uuid>", "decision": "like" | "nope" }

    Returns:
        - decision="like" → { "match": { match_id, with_profile_id } | null }
        - decision="nope" → delegates to /skip/by-uuid logic; returns {}
    """
    if s.person_id is None:
        return "Not signed in", 401

    if req.decision == "nope":
        # Delegate to the existing skip path so we share its rate-limit
        # + report semantics. Skip is fire-and-forget; we just hand back {}.
        from service import person as _person
        _person.post_skip_by_uuid(
            t.PostSkip(report_reason=None),
            s,
            req.profile_uuid,
        )
        return {"match": None}

    if req.decision != "like":
        return "Unknown decision", 400

    with api_tx() as tx:
        rows = tx.execute(
            Q_RECORD_LIKE,
            dict(me_id=s.person_id, prospect_uuid=req.profile_uuid),
        ).fetchall()

    if not rows:
        # No mutual like (yet). Like was recorded but no match.
        return {"match": None}

    row = rows[0]
    return {
        "match": {
            "match_id": row["match_id"],
            "with_profile_id": row["peer_uuid"],
        }
    }


def get_matches(s: t.SessionInfo):
    """List mutual matches for the session user."""
    if s.person_id is None:
        return "Not signed in", 401

    with api_tx() as tx:
        rows = tx.execute(Q_LIST_MATCHES, dict(me_id=s.person_id)).fetchall()

    matches = [
        {
            "match_id": r["match_id"],
            "with_profile": {
                "id": r["peer_uuid"],
                "firstName": r["peer_name"],
                "age": r["peer_age"],
                "photo_uuids": r["peer_photo_uuids"],
            },
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"matches": matches}


def get_incoming_likes(s: t.SessionInfo):
    """Users who liked the session user but for whom the session user
    has not yet decided. Powers the /matches 'Liked you' tab. Same
    response shape as get_matches with `with_profile` for frontend
    parity, plus `liked_at` so the UI can sort by recency."""
    if s.person_id is None:
        return "Not signed in", 401

    with api_tx() as tx:
        rows = tx.execute(Q_LIST_INCOMING_LIKES, dict(me_id=s.person_id)).fetchall()

    likes = [
        {
            "with_profile": {
                "id": r["liker_uuid"],
                "firstName": r["liker_name"],
                "age": r["liker_age"],
                "photo_uuids": r["liker_photo_uuids"],
            },
            "liked_at": r["created_at"],
        }
        for r in rows
    ]
    return {"likes": likes}


def get_match(s: t.SessionInfo, match_id: str):
    """Single match record + the matched peer's profile."""
    if s.person_id is None:
        return "Not signed in", 401

    with api_tx() as tx:
        rows = tx.execute(
            Q_GET_MATCH,
            dict(me_id=s.person_id, match_id=match_id),
        ).fetchall()

    if not rows:
        return "Match not found", 404

    r = rows[0]
    return {
        "match_id": r["match_id"],
        "with_profile": {
            "id": r["peer_uuid"],
            "firstName": r["peer_name"],
            "age": r["peer_age"],
            "photo_uuids": r["peer_photo_uuids"],
        },
        "created_at": r["created_at"],
    }
