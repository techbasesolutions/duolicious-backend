"""SQL constants for the admin dashboard, split per concern to keep
each section under ~100 lines. Each submodule re-exports its
constants here for service.admin.* convenience."""
# Submodules added as each phase lands. Phase 0 only needs the gate
# + audit helper queries below.

Q_IS_ADMIN = """
    SELECT 1 = ANY (
        SELECT 1 FROM person
         WHERE uuid = %(uuid)s
           AND 'admin' = ANY(coalesce(roles, ARRAY[]::text[]))
    ) AS is_admin
"""

Q_INSERT_AUDIT = """
    INSERT INTO admin_audit_log
        (actor_email, actor_uuid, action, target_email, target_uuid, metadata)
    VALUES
        (%(actor_email)s, %(actor_uuid)s, %(action)s,
         %(target_email)s, %(target_uuid)s, %(metadata)s)
    RETURNING id
"""

from service.admin.queries.overview import (
    Q_OVERVIEW_KPIS,
    Q_OVERVIEW_SIGNUPS_30D,
    Q_OVERVIEW_REFERRAL_CTR_7D,
    Q_OVERVIEW_PREMIUM_30D,
    Q_OVERVIEW_RECENT_ACTIVITY,
)

from service.admin.queries.users import (
    Q_USERS_LIST,
    Q_USERS_COUNT,
    Q_USER_PROFILE,
    Q_USER_PHOTOS,
    Q_USER_TOKEN_LEDGER,
    Q_USER_TOKEN_BALANCE,
    Q_USER_REFERRAL_STATS,
    Q_USER_AUDIT_LOG,
    Q_USER_WAITLIST_ANSWERS,
)

from service.admin.queries.cohorts import (
    Q_WAITLIST_KPIS, Q_WAITLIST_VELOCITY_30D,
    Q_WAITLIST_SEX, Q_WAITLIST_INTENT, Q_WAITLIST_COUNTRY,
    Q_WAITLIST_ETHNICITY, Q_WAITLIST_ASSEMBLY, Q_WAITLIST_SOURCE,
    Q_BETA_KPIS, Q_BETA_ROWS, Q_BETA_FUNNEL,
    Q_REFERRALS_KPIS, Q_REFERRALS_CLICK_STREAM_7D, Q_REFERRALS_PER_INVITER,
)
