"""SQL for Moderation tab — photos + rude messages.

Reports sub-tab continues to use the existing /admin/reports surface
from service/api/moderation_routes.py — we DON'T reimplement here."""

Q_PHOTOS_LIST = """
    SELECT
        ph.uuid AS photo_uuid,
        ph.position,
        ph.moderation_status::text AS moderation_status,
        ph.nsfw_score,
        ph.moderated_at,
        p.email AS owner_email,
        p.uuid::text AS owner_uuid
      FROM photo ph
      JOIN person p ON p.id = ph.person_id
     WHERE (%(state)s = '' OR ph.moderation_status::text = %(state)s)
     ORDER BY ph.moderated_at DESC NULLS FIRST
     LIMIT 100
"""

Q_PHOTOS_KPIS = """
    SELECT
        COUNT(*) FILTER (WHERE moderation_status::text = 'pending') AS pending,
        COUNT(*) FILTER (WHERE moderation_status::text = 'manual_review') AS manual_review,
        COUNT(*) FILTER (WHERE moderation_status::text = 'approved') AS approved,
        COUNT(*) FILTER (WHERE moderation_status::text = 'rejected') AS rejected
      FROM photo
"""

# rude_message schema (init-api.sql:1675):
#   person_id INT, created_at TIMESTAMP, message TEXT
# This is the *sender's* flagged outgoing message — no recipient is stored.
# We surface sender email + message snippet so the admin can see what got
# blocked. The FE shape uses sender_email + a single "recipient" column
# left empty (the table has no recipient).
Q_RUDE_MESSAGES = """
    SELECT
        rm.created_at,
        p.email AS sender_email,
        ''::text AS recipient_email,
        rm.message AS snippet
      FROM rude_message rm
      JOIN person p ON p.id = rm.person_id
     ORDER BY rm.created_at DESC
     LIMIT 100
"""
