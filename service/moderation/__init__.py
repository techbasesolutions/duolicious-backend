"""
service.moderation - admin/mod-gated abuse-report listing.

Phase W cutover (2026-05-15) closes the gap that DUO_REPORT_EMAIL
points at placeholder bouncing addresses (ahavah@example.com etc).
Until that env points at a real mailbox, abuse reports persist only
in the `skipped` table with `reported = TRUE` + `report_reason`. This
module surfaces those rows to operators with `admin` or `mod` in
person.roles via GET /admin/reports.

Auth model:
- Standard session check via the @aget decorator (must be signed in
  + onboarded — abuse moderation can't be done by anonymous viewers).
- Role gate inside the handler: person.roles && ARRAY['admin','mod'].
  Returns 403 for non-admin signed-in users.

Read-only for now. Future enhancement: POST /admin/reports/<id>/resolve
to mark as handled, requires a `resolved_at TIMESTAMPTZ` column added
to `skipped` (or a separate `report_resolution` table). Soft-launch
operators can manage state out-of-band (sticky-note + the SQL query
in this docstring).
"""

from database import api_tx


# ---------------------------------------------------------------------------
# Q_IS_ADMIN_OR_MOD - 1-row gate-check used by every handler in this module
# ---------------------------------------------------------------------------
Q_IS_ADMIN_OR_MOD = """
SELECT 1
  FROM person
 WHERE id = %(person_id)s
   AND roles && ARRAY['admin', 'mod']::TEXT[]
"""


# ---------------------------------------------------------------------------
# Q_LIST_REPORTS - reporter + reported user info + reason + age
# ---------------------------------------------------------------------------
#
# Joins skipped twice: once to the REPORTER (subject_person_id) and once
# to the REPORTED (object_person_id). Filters on reported = TRUE because
# `skipped` rows without a report_reason are just regular blocks/passes
# that aren't moderator-actionable.
#
# Limit 200 - if you have more than 200 outstanding moderation actions,
# you need a real ticketing tool, not a SQL view.

Q_LIST_REPORTS = """
SELECT
    s.created_at::text                          AS created_at,
    s.subject_person_id                          AS reporter_id,
    reporter.uuid::text                          AS reporter_uuid,
    reporter.name                                AS reporter_name,
    reporter.email                               AS reporter_email,
    s.object_person_id                           AS reported_id,
    reported.uuid::text                          AS reported_uuid,
    reported.name                                AS reported_name,
    reported.email                               AS reported_email,
    reported.activated                           AS reported_active,
    reported.verification_required               AS reported_verification_required,
    s.report_reason                              AS reason
FROM   skipped s
JOIN   person reporter ON reporter.id = s.subject_person_id
JOIN   person reported ON reported.id = s.object_person_id
WHERE  s.reported = TRUE
ORDER BY s.created_at DESC
LIMIT 200
"""


def get_admin_reports(s):
    """Returns recent abuse reports for admin/mod review.

    Response shape:
      200: {"reports": [{...}, ...], "count": N}
      401: not signed in
      403: signed in but not admin/mod

    Each report dict carries:
      created_at, reporter_id, reporter_uuid, reporter_name,
      reporter_email, reported_id, reported_uuid, reported_name,
      reported_email, reported_active, reported_verification_required,
      reason
    """
    if not s or not s.person_id:
        return 'Not authorized', 401

    with api_tx('read committed') as tx:
        is_admin = tx.execute(
            Q_IS_ADMIN_OR_MOD,
            dict(person_id=s.person_id),
        ).fetchone()
        if not is_admin:
            return 'Forbidden', 403

        rows = tx.execute(Q_LIST_REPORTS).fetchall()

    # rows is a list of dict-like objects; psycopg's row_factory makes
    # them already JSON-serializable. Wrap in a count for the frontend
    # so an empty list doesn't look like a fetch failure.
    return {
        'reports': [dict(r) for r in rows],
        'count': len(rows),
    }
