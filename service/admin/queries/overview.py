"""SQL for GET /admin/overview — the dashboard's top-of-page KPIs +
3 chart series + recent activity feed."""

Q_OVERVIEW_KPIS = """
    SELECT
        (SELECT COUNT(*) FROM waitlist_signup WHERE created_at::date = CURRENT_DATE)
            + (SELECT COUNT(*) FROM beta_signup WHERE created_at::date = CURRENT_DATE) AS signups_today,
        (SELECT COUNT(*) FROM person WHERE last_online_time > NOW() - INTERVAL '24 hours') AS active_24h,
        (SELECT COUNT(*) FROM person) AS person_total,
        (SELECT COUNT(*) FROM person WHERE 'premium' = ANY(entitlements)) AS premium_holders,
        (SELECT COUNT(*) FROM skipped WHERE reported = TRUE) AS pending_reports,
        (SELECT COUNT(*) FROM referral WHERE created_at > NOW() - INTERVAL '7 days') AS referral_signups_7d
"""

Q_OVERVIEW_SIGNUPS_30D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '29 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((SELECT COUNT(*) FROM waitlist_signup WHERE created_at::date = days.d), 0) AS waitlist,
        COALESCE((SELECT COUNT(*) FROM beta_signup WHERE created_at::date = days.d), 0) AS beta,
        COALESCE((SELECT COUNT(*) FROM person WHERE sign_up_time::date = days.d), 0) AS person
      FROM days
     ORDER BY days.d
"""

Q_OVERVIEW_REFERRAL_CTR_7D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '6 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((SELECT COUNT(*) FROM referral_link_click WHERE created_at::date = days.d AND user_agent_class != 'bot'), 0) AS clicks,
        COALESCE((SELECT COUNT(*) FROM referral WHERE created_at::date = days.d), 0) AS signups
      FROM days
     ORDER BY days.d
"""

Q_OVERVIEW_PREMIUM_30D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '29 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((
            SELECT COUNT(*) FROM admin_audit_log
             WHERE action = 'grant_entitlement'
               AND metadata->>'name' = 'premium'
               AND created_at::date = days.d
        ), 0) AS adds
      FROM days
     ORDER BY days.d
"""

Q_OVERVIEW_RECENT_ACTIVITY = """
    SELECT * FROM (
        SELECT 'signup' AS kind, email AS subject, NULL::text AS object,
               created_at FROM beta_signup ORDER BY created_at DESC LIMIT 10
    ) sub_beta
    UNION ALL SELECT * FROM (
        SELECT 'signup_waitlist' AS kind, email AS subject, NULL::text AS object,
               created_at FROM waitlist_signup ORDER BY created_at DESC LIMIT 10
    ) sub_wl
    UNION ALL SELECT * FROM (
        SELECT 'referral_credited' AS kind, inviter_email AS subject, invitee_email AS object,
               credited_at AS created_at FROM referral
         WHERE status = 'credited' AND credited_at IS NOT NULL
         ORDER BY credited_at DESC LIMIT 10
    ) sub_ref
    UNION ALL SELECT * FROM (
        SELECT 'admin_action' AS kind, actor_email AS subject, action AS object,
               created_at FROM admin_audit_log ORDER BY created_at DESC LIMIT 10
    ) sub_admin
    ORDER BY created_at DESC LIMIT 20
"""
