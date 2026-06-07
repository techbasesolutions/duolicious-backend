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
