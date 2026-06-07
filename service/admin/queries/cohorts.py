"""SQL for the 3 Cohorts sub-tabs."""

# ---- WAITLIST -----------------------------------------------------------

Q_WAITLIST_KPIS = """
    SELECT
        (SELECT COUNT(*) FROM waitlist_signup) AS total,
        (SELECT COUNT(*) FROM waitlist_signup WHERE answers <> '{}'::jsonb) AS complete,
        (SELECT COUNT(*) FROM waitlist_signup WHERE answers = '{}'::jsonb) AS empty,
        (SELECT COUNT(*) FROM waitlist_signup WHERE created_at::date = CURRENT_DATE) AS today,
        (SELECT COUNT(*) FROM waitlist_signup WHERE created_at > NOW() - INTERVAL '7 days') AS this_week
"""

# 30-day signup velocity, stacked across the 3 sources.
Q_WAITLIST_VELOCITY_30D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '29 days')::date,
            CURRENT_DATE, INTERVAL '1 day'
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

# Demographics breakdowns. Each returns label + count rows for a bar chart.
Q_WAITLIST_SEX = """
    SELECT COALESCE(answers->>'sex', '(none)') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE answers <> '{}'::jsonb
     GROUP BY 1 ORDER BY 2 DESC
"""

Q_WAITLIST_INTENT = """
    SELECT jsonb_array_elements_text(answers->'intent') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE jsonb_typeof(answers->'intent') = 'array'
     GROUP BY 1 ORDER BY 2 DESC LIMIT 15
"""

Q_WAITLIST_COUNTRY = """
    SELECT COALESCE(answers->>'country', '(none)') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE answers <> '{}'::jsonb
     GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""

Q_WAITLIST_ETHNICITY = """
    SELECT COALESCE(answers->>'ethnicity', '(none)') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE answers <> '{}'::jsonb
     GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""

Q_WAITLIST_ASSEMBLY = """
    SELECT jsonb_array_elements_text(answers->'assembly') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE jsonb_typeof(answers->'assembly') = 'array'
     GROUP BY 1 ORDER BY 2 DESC LIMIT 15
"""

Q_WAITLIST_SOURCE = """
    SELECT COALESCE(answers->>'referral_source', '(none)') AS label, COUNT(*) AS count
      FROM waitlist_signup
     WHERE answers <> '{}'::jsonb
     GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""

# ---- BETA ---------------------------------------------------------------

Q_BETA_KPIS = """
    SELECT
        (SELECT COUNT(*) FROM beta_signup) AS cohort_size,
        (SELECT COUNT(*) FROM beta_signup b
            WHERE EXISTS (SELECT 1 FROM person p WHERE p.email = b.email)) AS completed_onboarding,
        (SELECT COUNT(*) FROM beta_signup WHERE referral_code IS NOT NULL) AS codes_minted,
        (SELECT COUNT(*) FROM beta_signup WHERE referral_intro_sent_at IS NOT NULL) AS intro_sent,
        (SELECT COUNT(*) FROM beta_signup WHERE referral_intro_sent_at IS NULL) AS never_emailed
"""

Q_BETA_ROWS = """
    SELECT
        b.email,
        b.referral_code,
        b.referral_intro_sent_at,
        b.unsubscribed_at,
        (p.uuid IS NOT NULL) AS has_person,
        p.sign_in_time
      FROM beta_signup b
      LEFT JOIN person p ON p.email = b.email
     ORDER BY b.created_at DESC
     LIMIT 100
"""

Q_BETA_FUNNEL = """
    SELECT
        (SELECT COUNT(*) FROM waitlist_signup) AS waitlist_total,
        (SELECT COUNT(*) FROM beta_signup) AS beta_total,
        (SELECT COUNT(*) FROM beta_signup b
            WHERE EXISTS (SELECT 1 FROM person p WHERE p.email = b.email)) AS person_from_beta,
        (SELECT COUNT(*) FROM person p
            WHERE EXISTS (SELECT 1 FROM beta_signup b WHERE b.email = p.email)) AS completed_onboarding
"""

# ---- REFERRALS ----------------------------------------------------------

Q_REFERRALS_KPIS = """
    SELECT
        (SELECT COUNT(*) FROM referral_link_click) AS total_clicks,
        (SELECT COUNT(DISTINCT code) FROM referral_link_click WHERE code IS NOT NULL) AS distinct_codes_clicked,
        (SELECT COUNT(*) FROM referral) AS signups_via_link,
        (SELECT COUNT(*) FROM referral WHERE status = 'credited') AS credits_fired
"""

# Click stream by UA class, 7d.
Q_REFERRALS_CLICK_STREAM_7D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '6 days')::date,
            CURRENT_DATE, INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((SELECT COUNT(*) FROM referral_link_click WHERE created_at::date = days.d AND user_agent_class = 'mobile'), 0) AS mobile,
        COALESCE((SELECT COUNT(*) FROM referral_link_click WHERE created_at::date = days.d AND user_agent_class = 'desktop'), 0) AS desktop,
        COALESCE((SELECT COUNT(*) FROM referral_link_click WHERE created_at::date = days.d AND user_agent_class = 'bot'), 0) AS bot
      FROM days
     ORDER BY days.d
"""

Q_REFERRALS_PER_INVITER = """
    SELECT
        bs.email AS inviter,
        bs.referral_code,
        COUNT(DISTINCT rc.id) AS clicks,
        COUNT(DISTINCT r.id) AS signups,
        COUNT(DISTINCT r.id) FILTER (WHERE r.status = 'credited') AS credited,
        (COUNT(DISTINCT r.id) FILTER (WHERE r.status IN ('pending','graduated'))) * 5 AS pending_token_balance
      FROM beta_signup bs
      LEFT JOIN referral_link_click rc ON rc.inviter_email = bs.email
      LEFT JOIN referral r ON r.inviter_email = bs.email
     WHERE bs.referral_intro_sent_at IS NOT NULL
     GROUP BY bs.email, bs.referral_code
     ORDER BY clicks DESC NULLS LAST, signups DESC NULLS LAST
     LIMIT 50
"""
