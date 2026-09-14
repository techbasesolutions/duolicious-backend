"""Weekly roundup tile snapshot (plan spec 3.3, 3.5 E2, Task 11).

Called by POST /admin/growth/spotlight/roundup at creation time, once per
request (not per platform row) -- the resulting dict is stored verbatim as
the `payload` jsonb column on both platform rows so GET /admin/growth/queue
can serve it back without recomputing anything.
"""
from __future__ import annotations

from service.growth.queries import _excluded
from service.spotlight.eligibility import eligibility, photo_url

MAX_TILES = 4

# One row per person even though a member may carry two publishing_queue rows
# for their approved welcome card (one per platform, both stamped with the
# same approved_photo_uuid by set_member_approval) -- the photo is pulled via
# a correlated subquery rather than a JOIN so no de-duplication is needed.
_Q_CANDIDATES = """
    SELECT p.id,
           split_part(p.name, ' ', 1) AS first_name,
           (SELECT q.approved_photo_uuid::text
              FROM publishing_queue q
             WHERE q.subject_person_id = p.id
               AND q.kind = 'welcome'
               AND q.member_approved_at IS NOT NULL
               AND q.status <> 'cancelled'
             ORDER BY q.member_approved_at DESC
             LIMIT 1) AS photo_uuid
      FROM person p
     WHERE p.sign_up_time > NOW() - make_interval(days => %(days)s)
       AND EXISTS (SELECT 1 FROM publishing_queue q
                    WHERE q.subject_person_id = p.id
                      AND q.kind = 'welcome'
                      AND q.member_approved_at IS NOT NULL
                      AND q.status <> 'cancelled')
     ORDER BY p.sign_up_time DESC
"""

_Q_TOTALS = """
    SELECT count(*) AS n,
           count(DISTINCT country) AS countries
      FROM person
     WHERE activated
       AND sign_up_time > NOW() - make_interval(days => %(days)s)
       AND lower(email) <> ALL(%(ex)s)
"""


def roundup_snapshot(tx, days: int = 7) -> dict:
    tiles = []
    for r in tx.execute(_Q_CANDIDATES, dict(days=days)).fetchall():
        if not r['photo_uuid']:
            continue
        ok, _reason = eligibility(tx, r['id'])
        if not ok:
            continue
        tiles.append(dict(
            person_id=r['id'],
            first_name=r['first_name'],
            photo_url=photo_url(r['photo_uuid'], 450),
        ))
        if len(tiles) == MAX_TILES:
            break
    totals = tx.execute(_Q_TOTALS, dict(days=days, ex=_excluded())).fetchone()
    return dict(tiles=tiles, count=int(totals['n']), countries=int(totals['countries']))
