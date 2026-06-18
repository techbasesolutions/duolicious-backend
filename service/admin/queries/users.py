"""SQL for GET /admin/users (list) and GET /admin/users/:uuid (detail)."""

# Unified "users" rowset across person + beta_signup + waitlist_signup.
# Keyed on email. The same email can appear in multiple source tables;
# we coalesce. Returns paginated rows + total count.
#
# Filters supported:
#   has_person       — only rows with a person row
#   waitlist_only    — has waitlist row but NO person row
#   beta_only        — has beta row but NO person row
#   unsubscribed     — beta or waitlist unsubscribed_at IS NOT NULL
#   has_premium      — person.entitlements contains 'premium'
#   role_admin       — 'admin' = ANY(person.roles)
#   role_mod         — 'mod' = ANY(person.roles)
#
# Search q: ILIKE prefix match against email / person.name / person.uuid::text.
Q_USERS_LIST = """
    WITH unified AS (
        SELECT
            COALESCE(p.email, bs.email, ws.email) AS email,
            p.uuid::text AS uuid,
            p.name AS name,
            p.sign_up_time,
            COALESCE(p.roles, ARRAY[]::text[]) AS roles,
            COALESCE(p.entitlements, ARRAY[]::text[]) AS entitlements,
            COALESCE(p.activated, TRUE) AS activated,
            (bs.email IS NOT NULL) AS in_beta,
            (ws.email IS NOT NULL) AS in_waitlist,
            (p.email IS NOT NULL) AS has_person,
            COALESCE(bs.unsubscribed_at, ws.unsubscribed_at) AS unsubscribed_at
          FROM person p
          FULL OUTER JOIN beta_signup bs ON bs.email = p.email
          FULL OUTER JOIN waitlist_signup ws ON ws.email = COALESCE(p.email, bs.email)
    )
    SELECT *
      FROM unified
     WHERE
        (%(q)s = '' OR
         email ILIKE %(q_like)s OR
         COALESCE(name, '') ILIKE %(q_like)s OR
         COALESCE(uuid, '') ILIKE %(q_like)s)
       AND (NOT %(has_person)s OR has_person)
       AND (NOT %(waitlist_only)s OR (in_waitlist AND NOT has_person))
       AND (NOT %(beta_only)s OR (in_beta AND NOT has_person))
       AND (NOT %(unsubscribed)s OR unsubscribed_at IS NOT NULL)
       AND (NOT %(has_premium)s OR 'premium' = ANY(entitlements))
       AND (NOT %(role_admin)s OR 'admin' = ANY(roles))
       AND (NOT %(role_mod)s OR 'mod' = ANY(roles))
     ORDER BY COALESCE(sign_up_time, NOW()) DESC NULLS LAST, email
     LIMIT %(limit)s OFFSET %(offset)s
"""

Q_USERS_COUNT = """
    WITH unified AS (
        SELECT
            COALESCE(p.email, bs.email, ws.email) AS email,
            p.uuid::text AS uuid,
            p.name AS name,
            COALESCE(p.roles, ARRAY[]::text[]) AS roles,
            COALESCE(p.entitlements, ARRAY[]::text[]) AS entitlements,
            (bs.email IS NOT NULL) AS in_beta,
            (ws.email IS NOT NULL) AS in_waitlist,
            (p.email IS NOT NULL) AS has_person,
            COALESCE(bs.unsubscribed_at, ws.unsubscribed_at) AS unsubscribed_at
          FROM person p
          FULL OUTER JOIN beta_signup bs ON bs.email = p.email
          FULL OUTER JOIN waitlist_signup ws ON ws.email = COALESCE(p.email, bs.email)
    )
    SELECT COUNT(*) AS total
      FROM unified
     WHERE
        (%(q)s = '' OR
         email ILIKE %(q_like)s OR
         COALESCE(name, '') ILIKE %(q_like)s OR
         COALESCE(uuid, '') ILIKE %(q_like)s)
       AND (NOT %(has_person)s OR has_person)
       AND (NOT %(waitlist_only)s OR (in_waitlist AND NOT has_person))
       AND (NOT %(beta_only)s OR (in_beta AND NOT has_person))
       AND (NOT %(unsubscribed)s OR unsubscribed_at IS NOT NULL)
       AND (NOT %(has_premium)s OR 'premium' = ANY(entitlements))
       AND (NOT %(role_admin)s OR 'admin' = ANY(roles))
       AND (NOT %(role_mod)s OR 'mod' = ANY(roles))
"""

# Single-user blob: person row + photos + entitlements + last-20 tokens +
# referral counts + per-user audit log + waitlist answers (if any).
Q_USER_PROFILE = """
    SELECT
        p.uuid::text AS uuid,
        p.email,
        p.name,
        p.about,
        p.date_of_birth,
        p.country,
        p.region,
        p.languages_spoken,
        p.primary_language,
        p.sign_up_time,
        p.sign_in_time,
        p.sign_in_count,
        p.last_online_time,
        p.activated,
        COALESCE(p.roles, ARRAY[]::text[]) AS roles,
        COALESCE(p.entitlements, ARRAY[]::text[]) AS entitlements,
        p.subscription_expires_at,
        p.ahavah_verification_tier::text AS verification_tier,
        p.deletion_requested_at,
        p.location_short_friendly AS location_short,
        p.location_long_friendly AS location_long
      FROM person p
     WHERE p.uuid = %(uuid)s::uuid
"""

Q_USER_PHOTOS = """
    SELECT
        position,
        moderation_status::text AS moderation_state,
        nsfw_score,
        moderated_at AS created_at,
        uuid AS photo_uuid
      FROM photo
     WHERE person_id = (SELECT id FROM person WHERE uuid = %(uuid)s::uuid)
     ORDER BY position
"""

Q_USER_TOKEN_LEDGER = """
    SELECT
        delta, reason, metadata, created_at
      FROM token_ledger
     WHERE person_id = %(uuid)s
     ORDER BY created_at DESC
     LIMIT 20
"""

Q_USER_TOKEN_BALANCE = """
    SELECT COALESCE(SUM(delta), 0)::int AS balance
      FROM token_ledger
     WHERE person_id = %(uuid)s
"""

Q_USER_REFERRAL_STATS = """
    SELECT
        COUNT(*) FILTER (WHERE status = 'pending')   AS pending,
        COUNT(*) FILTER (WHERE status = 'graduated') AS graduated,
        COUNT(*) FILTER (WHERE status = 'credited')  AS credited
      FROM referral
     WHERE inviter_email = (SELECT email FROM person WHERE uuid = %(uuid)s::uuid)
"""

Q_USER_AUDIT_LOG = """
    SELECT
        actor_email,
        action,
        metadata,
        created_at
      FROM admin_audit_log
     WHERE target_uuid = %(uuid)s::uuid
        OR target_email = (SELECT email FROM person WHERE uuid = %(uuid)s::uuid)
     ORDER BY created_at DESC
     LIMIT 50
"""

Q_USER_WAITLIST_ANSWERS = """
    SELECT answers
      FROM waitlist_signup
     WHERE email = (SELECT email FROM person WHERE uuid = %(uuid)s::uuid)
"""

# GET /admin/map — every activated user as an individual point, for the admin
# "Show everyone" view. UNFILTERED (no verified/gender/age/skip) and ignores
# the showOnMap opt-out (admin oversight sees everyone). The frontend clusters
# + spiderfies client-side, same as the normal map.
Q_ADMIN_MAP_MARKERS = """
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
    FROM person p
    WHERE p.activated = TRUE
      -- Same as the public map: hide country-only (citySet=false) users who
      -- sit on a country-centroid default rather than a real city, so they
      -- don't read as a fake cluster. They return once they set a city.
      AND COALESCE((p.ahavah_extra->>'citySet')::boolean, FALSE)
"""
