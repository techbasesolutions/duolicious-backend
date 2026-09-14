"""Spotlight eligibility (spec 3.2): one SQL statement, first failing reason wins."""
from __future__ import annotations
from typing import Optional
from service.config import USER_IMAGES_BASE_URL

REASONS = ('not_activated', 'not_opted_in', 'not_verified', 'under_18', 'reported',
           'pending_deletion', 'featured_recently', 'no_photo')

_Q = """
    SELECT p.activated,
           p.spotlight_opt_in,
           (p.ahavah_verification_tier IS NOT NULL AND p.ahavah_verification_tier::text <> 'none') AS verified,
           (p.date_of_birth IS NOT NULL AND p.date_of_birth <= (NOW() - interval '18 years')::date) AS adult,
           EXISTS (SELECT 1 FROM skipped s WHERE s.object_person_id = p.id AND s.reported = TRUE) AS reported,
           (p.deletion_requested_at IS NOT NULL) AS pending_deletion,
           (p.spotlight_last_featured_at IS NOT NULL AND p.spotlight_last_featured_at > NOW() - interval '30 days') AS featured_recently,
           EXISTS (SELECT 1 FROM photo ph WHERE ph.person_id = p.id AND ph.moderation_status = 'approved') AS has_photo
      FROM person p WHERE p.id = %(pid)s
"""


def eligibility(tx, person_id: int) -> tuple[bool, str]:
    r = tx.execute(_Q, dict(pid=person_id)).fetchone()
    if not r:
        return False, 'not_activated'
    checks = [
        ('not_activated', not r['activated']),
        ('not_opted_in', not r['spotlight_opt_in']),
        ('not_verified', not r['verified']),
        ('under_18', not r['adult']),
        ('reported', r['reported']),
        ('pending_deletion', r['pending_deletion']),
        ('featured_recently', r['featured_recently']),
        ('no_photo', not r['has_photo']),
    ]
    for reason, failed in checks:
        if failed:
            return False, reason
    return True, ''


def primary_photo_uuid(tx, person_id: int) -> Optional[str]:
    r = tx.execute("SELECT uuid::text AS u FROM photo WHERE person_id = %(pid)s AND moderation_status = 'approved' ORDER BY position LIMIT 1", dict(pid=person_id)).fetchone()
    return r['u'] if r else None


def photo_url(uuid: str, size: Optional[int] = None) -> str:
    return f"{USER_IMAGES_BASE_URL}/{size or 'original'}-{uuid}.jpg"
