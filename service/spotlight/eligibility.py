"""Spotlight eligibility (spec 3.2): one SQL statement, first failing reason wins."""
from __future__ import annotations
from typing import Optional
from service.config import USER_IMAGES_BASE_URL

REASONS = ('not_activated', 'not_opted_in', 'not_verified', 'under_18', 'reported',
           'pending_deletion', 'featured_recently', 'no_photo', 'photo_missing')

_Q = """
    SELECT p.activated,
           p.spotlight_opt_in,
           (p.ahavah_verification_tier IS NOT NULL AND p.ahavah_verification_tier::text <> 'none') AS verified,
           (p.date_of_birth IS NOT NULL AND p.date_of_birth <= (NOW() - interval '18 years')::date) AS adult,
           EXISTS (SELECT 1 FROM skipped s WHERE s.object_person_id = p.id AND s.reported = TRUE) AS reported,
           (p.deletion_requested_at IS NOT NULL) AS pending_deletion,
           EXISTS (SELECT 1 FROM spotlight_occurrence o WHERE o.person_id = p.id
                     AND o.created_at > NOW() - interval '30 days'
                     AND (%(xrk)s::text IS NULL OR o.request_key <> %(xrk)s::text)) AS featured_recently,
           EXISTS (SELECT 1 FROM photo ph WHERE ph.person_id = p.id AND ph.moderation_status = 'approved') AS has_photo
      FROM person p WHERE p.id = %(pid)s
"""


def eligibility(tx, person_id: int, photo_uuid: Optional[str] = None, *,
                 exclude_request_key: Optional[str] = None) -> tuple[bool, str]:
    r = tx.execute(_Q, dict(pid=person_id, xrk=exclude_request_key)).fetchone()
    if not r:
        return False, 'not_activated'
    if photo_uuid is not None:
        # A specific photo was chosen (the one a rendered card actually
        # shows), so its own presence, ownership and moderation status is
        # what matters here -- a different approved photo on the same
        # person does not save a card whose chosen photo is gone.
        photo_reason = 'photo_missing'
        photo_failed = tx.execute(
            """SELECT 1 FROM photo WHERE uuid::text = %(u)s AND person_id = %(pid)s
                AND moderation_status = 'approved'""",
            dict(u=photo_uuid, pid=person_id)).fetchone() is None
    else:
        photo_reason = 'no_photo'
        photo_failed = not r['has_photo']
    checks = [
        ('not_activated', not r['activated']),
        ('not_opted_in', not r['spotlight_opt_in']),
        ('not_verified', not r['verified']),
        ('under_18', not r['adult']),
        ('reported', r['reported']),
        ('pending_deletion', r['pending_deletion']),
        ('featured_recently', r['featured_recently']),
        (photo_reason, photo_failed),
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
