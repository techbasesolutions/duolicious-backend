"""SQL for the Audit Log tab — filterable view of admin_audit_log."""

Q_AUDIT_LIST = """
    SELECT
        actor_email, actor_uuid::text AS actor_uuid,
        action, target_email, target_uuid::text AS target_uuid,
        metadata, created_at
      FROM admin_audit_log
     WHERE (%(actor)s = '' OR actor_email = %(actor)s)
       AND (%(target)s = '' OR target_email = %(target)s)
       AND (%(action)s = '' OR action = %(action)s)
       AND created_at > NOW() - (%(days)s || ' days')::INTERVAL
     ORDER BY created_at DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""

Q_AUDIT_COUNT = """
    SELECT COUNT(*) AS total
      FROM admin_audit_log
     WHERE (%(actor)s = '' OR actor_email = %(actor)s)
       AND (%(target)s = '' OR target_email = %(target)s)
       AND (%(action)s = '' OR action = %(action)s)
       AND created_at > NOW() - (%(days)s || ' days')::INTERVAL
"""
