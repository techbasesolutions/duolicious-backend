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

# Verification jobs. Nothing in any of the three repos surfaced one of these
# before, so a selfie check that died left the member on a polling screen and
# the operator with no way to know it had happened.
#
#   queued      submitted, not yet picked up. Healthy for about a second.
#   running     in flight and inside its lease. Healthy. A lease is what
#               makes a run healthy, so a row with no lease at all is not
#               counted here: see `stuck`.
#   stuck       running and either past its lease or holding none. This is
#               the number that matters: when the cron is healthy it is
#               zero, and it goes non zero whether the cause is one dead
#               worker or the whole runner being down. A row here is a
#               member waiting with no answer.
#
#               A NULL lease belongs here rather than under `running`.
#               Migration 0053 stamped every row that existed and the claim
#               query always sets one, so it should be unreachable; if it
#               ever is reached, Q_ELIGIBLE_VERIFICATION_JOBS refuses to
#               reap it, so counting it as healthy would make it invisible
#               to the reaper and to the operator at once, which is the
#               worst of the two readings.
#   abandoned   the subset of `stuck` that has burned its retries. The cron
#               ends these and notifies the member, so this is a count of
#               rows waiting for that sweep and reads zero when the runner
#               is up. A number that stays non zero means nothing is
#               running the sweep.
#   failed      checks that did not pass, whether the classifier rejected
#               them or they died more times than they may be retried. Not
#               an error on its own, but a wall of them means the
#               classifier, not the members.
#
# `verification_job` rows are deleted at `expires_at`, which defaults to
# three days after the row is created, so every count here is a rolling
# three day window by design.
Q_VERIFICATION_JOBS = """
    SELECT
        COUNT(*) FILTER (
            WHERE status = 'queued'
        ) AS verification_queued,
        COUNT(*) FILTER (
            WHERE status = 'running'
              AND running_since IS NOT NULL
              AND running_since >= NOW() - make_interval(secs => %(lease_seconds)s)
        ) AS verification_running,
        COUNT(*) FILTER (
            WHERE status = 'running'
              AND (
                  running_since IS NULL
               OR running_since < NOW() - make_interval(secs => %(lease_seconds)s)
              )
        ) AS verification_stuck,
        COUNT(*) FILTER (
            WHERE status = 'running'
              AND running_since IS NOT NULL
              AND running_since < NOW() - make_interval(secs => %(lease_seconds)s)
              AND reap_count >= %(max_reaps)s
        ) AS verification_abandoned,
        COUNT(*) FILTER (
            WHERE status = 'failure'
        ) AS verification_failed
      FROM verification_job
"""
