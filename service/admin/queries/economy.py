"""SQL for the Economy tab — token ledger, entitlements, subscriptions."""

# Ledger explorer: paginated, filter by reason + email + range.
Q_LEDGER = """
    SELECT
        tl.created_at,
        tl.delta,
        tl.reason,
        tl.metadata,
        p.email,
        p.uuid::text AS person_uuid
      FROM token_ledger tl
      LEFT JOIN person p ON p.uuid = tl.person_id
     WHERE (%(reason)s = '' OR tl.reason = %(reason)s)
       AND (%(email)s = '' OR p.email ILIKE %(email_like)s)
       AND tl.created_at > NOW() - (%(days)s || ' days')::INTERVAL
     ORDER BY tl.created_at DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""

Q_LEDGER_COUNT = """
    SELECT COUNT(*) AS total
      FROM token_ledger tl
      LEFT JOIN person p ON p.uuid = tl.person_id
     WHERE (%(reason)s = '' OR tl.reason = %(reason)s)
       AND (%(email)s = '' OR p.email ILIKE %(email_like)s)
       AND tl.created_at > NOW() - (%(days)s || ' days')::INTERVAL
"""

Q_LEDGER_KPIS = """
    SELECT
        (SELECT COALESCE(SUM(delta), 0)::int FROM token_ledger WHERE delta > 0) AS total_credits,
        (SELECT COALESCE(SUM(delta), 0)::int FROM token_ledger WHERE delta < 0) AS total_debits,
        (SELECT COALESCE(SUM(delta), 0)::int FROM token_ledger) AS circulation,
        (SELECT COALESCE(SUM(delta), 0)::int FROM token_ledger WHERE created_at::date = CURRENT_DATE) AS today
"""

# Entitlement holders.
Q_ENTITLEMENT_HOLDERS = """
    SELECT
        p.email,
        p.uuid::text AS person_uuid,
        p.subscription_expires_at,
        (p.subscription_expires_at - NOW()) AS time_left,
        COALESCE(p.entitlements, ARRAY[]::text[]) AS entitlements
      FROM person p
     WHERE %(name)s = ANY(p.entitlements)
       AND (%(expires_within_days)s = 0 OR p.subscription_expires_at < NOW() + (%(expires_within_days)s || ' days')::INTERVAL)
     ORDER BY p.subscription_expires_at NULLS LAST
     LIMIT 200
"""

Q_ENTITLEMENT_KPIS = """
    SELECT
        COUNT(*) AS holders,
        COUNT(*) FILTER (WHERE subscription_expires_at < NOW() + INTERVAL '7 days') AS expiring_7d,
        COUNT(*) FILTER (WHERE subscription_expires_at < NOW() + INTERVAL '30 days') AS expiring_30d
      FROM person
     WHERE %(name)s = ANY(entitlements)
"""

# Subscriptions — keyed on person.stripe_customer_id + subscription_expires_at.
# No separate Stripe subs table exists in this codebase, so we infer state from person.
Q_SUBSCRIPTIONS_KPIS = """
    SELECT
        COUNT(*) FILTER (WHERE stripe_customer_id IS NOT NULL) AS total_stripe_customers,
        COUNT(*) FILTER (WHERE subscription_expires_at > NOW()) AS active,
        COUNT(*) FILTER (WHERE subscription_expires_at < NOW() AND stripe_customer_id IS NOT NULL) AS expired
      FROM person
"""

Q_SUBSCRIPTIONS_ROWS = """
    SELECT
        p.email,
        p.uuid::text AS person_uuid,
        p.stripe_customer_id,
        p.subscription_expires_at,
        COALESCE(p.entitlements, ARRAY[]::text[]) AS entitlements,
        CASE
            WHEN p.subscription_expires_at IS NULL THEN 'never'
            WHEN p.subscription_expires_at > NOW() THEN 'active'
            ELSE 'expired'
        END AS status
      FROM person p
     WHERE p.stripe_customer_id IS NOT NULL OR p.subscription_expires_at IS NOT NULL
     ORDER BY p.subscription_expires_at DESC NULLS LAST
     LIMIT 200
"""
