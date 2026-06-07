"""SQL for the System tab — operational health snapshot."""

Q_SYSTEM_HEALTH = """
    SELECT
        (SELECT COUNT(*) FROM person WHERE sign_up_time::date = CURRENT_DATE) AS signups_today,
        (SELECT COUNT(*) FROM person WHERE sign_up_time > NOW() - INTERVAL '7 days') AS signups_7d,
        (SELECT COUNT(*) FROM person WHERE sign_up_time > NOW() - INTERVAL '30 days') AS signups_30d,
        (SELECT COUNT(*) FROM person WHERE last_online_time > NOW() - INTERVAL '24 hours') AS dau,
        (SELECT COUNT(*) FROM person WHERE last_online_time > NOW() - INTERVAL '7 days') AS wau,
        (SELECT COUNT(*) FROM person WHERE last_online_time > NOW() - INTERVAL '30 days') AS mau,
        (SELECT COUNT(*) FROM waitlist_signup) AS waitlist_total,
        (SELECT COUNT(*) FROM beta_signup) AS beta_total,
        (SELECT COUNT(*) FROM photo) AS photo_total,
        (SELECT COUNT(*) FROM referral) AS referral_total,
        (SELECT COUNT(*) FROM admin_audit_log) AS admin_actions_total
"""

# OTP delivery: rough proxy — count of duo_session created in last 24h.
# Real "delivery success" requires a separate aws_smtp/Resend log integration
# (deferred per spec §F). Returning total sent so the UI has SOMETHING.
Q_OTP_24H = """
    SELECT COUNT(*) AS sent_24h
      FROM duo_session
     WHERE otp_expiry > NOW() - INTERVAL '24 hours'
"""
