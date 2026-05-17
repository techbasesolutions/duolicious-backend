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

from datetime import timedelta
from typing import Optional, Tuple
from database import api_tx
import duotypes as t


# Phase 5 (monetization-tokens) — daily like quota for free users.
#
# Plan deviation: the plan was written for an async/asyncpg stack; this
# codebase is sync psycopg. `_check_like_quota` takes a sync cursor `tx`
# and uses named placeholders. `liked.liker_id` is INT (person.id);
# `token_ledger.person_id` is UUID, hence the helper needs BOTH ids
# (caller passes person_id for liked-lookup, person_uuid for day_pass).
DAILY_LIKE_QUOTA = 10


_Q_COUNT_RECENT_LIKES = """
  SELECT COUNT(*) AS n FROM liked
   WHERE liker_id = %(liker_id)s
     AND created_at > NOW() - INTERVAL '24 hours'
"""

_Q_OLDEST_LIKE_IN_WINDOW = """
  SELECT MIN(created_at) AS oldest FROM liked
   WHERE liker_id = %(liker_id)s
     AND created_at > NOW() - INTERVAL '24 hours'
"""

_Q_HAS_ACTIVE_DAY_PASS = """
  SELECT 1 AS ok FROM token_ledger
   WHERE person_id = %(person_uuid)s
     AND reason = 'day_pass'
     AND (metadata->>'expires_at')::timestamptz > NOW()
   LIMIT 1
"""


def _check_like_quota(
    tx,
    person_id: int,
    person_uuid: str,
    entitlements: list,
) -> Optional[Tuple[int, dict]]:
    """Returns None if allowed, or (status_code, body) tuple if blocked.

    Premium entitlement bypasses the quota. An active day-pass ledger
    row (debit with reason='day_pass' and metadata.expires_at > NOW())
    also bypasses. Otherwise count likes in the last 24h; if >=
    DAILY_LIKE_QUOTA, return (429, {error, resets_at}).
    """
    if 'premium' in (entitlements or []):
        return None

    if tx.execute(
        _Q_HAS_ACTIVE_DAY_PASS,
        dict(person_uuid=person_uuid),
    ).fetchone():
        return None

    row = tx.execute(
        _Q_COUNT_RECENT_LIKES, dict(liker_id=person_id)
    ).fetchone()
    count = int(row['n']) if row else 0
    if count < DAILY_LIKE_QUOTA:
        return None

    oldest_row = tx.execute(
        _Q_OLDEST_LIKE_IN_WINDOW, dict(liker_id=person_id)
    ).fetchone()
    oldest = oldest_row['oldest'] if oldest_row else None
    resets_at = (oldest + timedelta(hours=24)).isoformat() if oldest else None
    return (429, {"error": "quota_exceeded", "resets_at": resets_at})


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
    EXTRACT(EPOCH FROM NOW() - peer.last_online_time)::int AS peer_seconds_since_last_online,
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
    EXTRACT(EPOCH FROM NOW() - liker.last_online_time)::int AS liker_seconds_since_last_online,
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
    EXTRACT(EPOCH FROM NOW() - peer.last_online_time)::int AS peer_seconds_since_last_online,
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

    # Phase 5: enforce 10/day like quota for free users; premium and
    # active day-pass bypass. Reuse the same api_tx for the quota check
    # and the like-insert so concurrent likes can't slip past the cap.
    from service.entitlements import list_entitlements
    assert s.person_uuid is not None
    entitlements = list_entitlements(s.person_id)

    with api_tx() as tx:
        blocked = _check_like_quota(
            tx, s.person_id, s.person_uuid, entitlements,
        )
        if blocked is not None:
            body, status = blocked[1], blocked[0]
            return body, status

        rows = tx.execute(
            Q_RECORD_LIKE,
            dict(me_id=s.person_id, prospect_uuid=req.profile_uuid),
        ).fetchall()

    if not rows:
        # No mutual like (yet). Like was recorded but no match.
        return {"match": None}

    row = rows[0]

    # Mutual like → push the matched peer. Lazy-import notifications +
    # wrapped in try/except so a missing pywebpush dependency, missing
    # VAPID env vars, or any other push-stack issue can never block
    # the match-create response. send_to_user_safe is itself
    # fire-and-forget but we belt-and-suspenders the import too.
    try:
        from service.notifications import send_to_user_safe
        with api_tx() as tx:
            me_row = tx.execute(
                "SELECT name FROM person WHERE id = %(me_id)s",
                dict(me_id=s.person_id),
            ).fetchone()
        my_name = (me_row or {}).get("name") or "Someone"
        send_to_user_safe(
            person_id=row["peer_id"],
            title="It's a match!",
            body=f"{my_name} likes you back",
            url="/matches",
            tag=f"match:{row['match_id']}",
            event_kind="match",
        )
    except Exception:
        import traceback
        print("decisions.post_decisions push trigger failed:")
        print(traceback.format_exc())

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
                "seconds_since_last_online": r["peer_seconds_since_last_online"],
            },
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"matches": matches}


_Q_REVEALED_LIKER_UUIDS = """
SELECT liker_id::text AS liker_uuid
  FROM revealed_likers
 WHERE viewer_id = %(viewer_id)s
"""


def get_incoming_likes(s: t.SessionInfo):
    """Users who liked the session user but for whom the session user
    has not yet decided. Powers the /matches 'Liked you' tab.

    Premium gate (Phase W cutover, 2026-05-15): the FULL list (names,
    ages, photo UUIDs) requires the 'premium' entitlement. Non-premium
    users see only the likers they've individually revealed via
    POST /tokens/reveal (Phase 4 monetization). The `count` field is
    always accurate so the locked-state CTA can render "N people like you".

    Response shape:
      Premium:   { "count": N, "likes": [...all],            "premium": true }
      Free tier: { "count": N, "likes": [...revealed only],  "premium": false }
    """
    if s.person_id is None:
        return "Not signed in", 401

    from service.entitlements import has_entitlement
    is_premium = has_entitlement(s.person_id, 'premium')

    with api_tx() as tx:
        rows = tx.execute(Q_LIST_INCOMING_LIKES, dict(me_id=s.person_id)).fetchall()
        # Phase 4: revealed_likers keys on the viewer's UUID, not int id.
        # Skip the second query entirely for premium users (they see all).
        revealed_uuids: set[str] = set()
        if not is_premium and s.person_uuid is not None:
            revealed_rows = tx.execute(
                _Q_REVEALED_LIKER_UUIDS,
                dict(viewer_id=str(s.person_uuid)),
            ).fetchall()
            revealed_uuids = {r['liker_uuid'] for r in revealed_rows}

    count = len(rows)

    def _visible(row) -> bool:
        return is_premium or row['liker_uuid'] in revealed_uuids

    # Phase 4 monetization-tokens (2026-05-16): non-premium users get
    # a HIDDEN stub for every unrevealed liker — `id` + `hidden: True`
    # only — so the frontend can render N tappable blurred cards (one
    # per real liker) and call POST /tokens/reveal with the correct
    # liker_id when the user taps to spend a token. Name/age/photos
    # remain server-side until the reveal lands.
    likes = []
    for r in rows:
        if _visible(r):
            likes.append({
                "with_profile": {
                    "id": r["liker_uuid"],
                    "firstName": r["liker_name"],
                    "age": r["liker_age"],
                    "photo_uuids": r["liker_photo_uuids"],
                    "seconds_since_last_online": r["liker_seconds_since_last_online"],
                },
                "liked_at": r["created_at"],
                "hidden": False,
            })
        else:
            likes.append({
                "with_profile": {
                    "id": r["liker_uuid"],
                    # Deliberately no firstName / age / photo_uuids —
                    # those are the paywalled fields.
                },
                "liked_at": r["created_at"],
                "hidden": True,
            })
    return {"count": count, "likes": likes, "premium": is_premium}


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
