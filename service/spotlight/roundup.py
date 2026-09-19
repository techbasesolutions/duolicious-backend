"""Weekly roundup tile snapshot (plan spec 3.3, 3.5 E2, Task 11; gated
count-only by default, Task 8).

Called by POST /admin/growth/spotlight/roundup at creation time, once per
request (not per platform row) -- the resulting dict is stored verbatim as
the `payload` jsonb column on both platform rows so GET /admin/growth/queue
can serve it back without recomputing anything.

Owner decision (Task 8): a roundup ships count-only -- no member tiles --
until a per-member roundup approval flow exists. `tiles` is therefore only
ever populated while the `roundup_tiles_enabled` setting is 'true' (seeded
'false'); `count`/`countries`/`country_names` are unaffected either way.
"""
from __future__ import annotations

from service.growth.queries import _excluded
from service.spotlight.country import display_country
from service.spotlight.eligibility import eligibility, photo_url

MAX_TILES = 4

# One row per person even though a member may carry two publishing_queue rows
# for their welcome card (one per platform, both pointing at the same
# current revision) -- the photo is pulled via a correlated subquery rather
# than a JOIN so no de-duplication is needed. A candidate is a member whose
# welcome request's CURRENT revision carries a 'subject' consent row for
# them (the approve_card path, Wave 1 F01); the dead approved_photo_uuid/
# member_approved_at columns on publishing_queue are no longer written
# (Task 2) and are never read here.
# `_excluded()` is applied here as well as in _Q_TOTALS below: a test account
# that opted in and approved a welcome card would otherwise be tiled onto a
# real published roundup while being left out of the count beside it.
_Q_CANDIDATES = """
    SELECT p.id,
           split_part(p.name, ' ', 1) AS first_name,
           (SELECT r.photo_uuid::text
              FROM publishing_queue q
              JOIN spotlight_revision r ON r.id = q.current_revision_id
              JOIN spotlight_revision_consent c
                ON c.revision_id = r.id AND c.person_id = p.id AND c.role = 'subject'
             WHERE q.subject_person_id = p.id
               AND q.kind = 'welcome'
               AND q.status <> 'cancelled'
             ORDER BY c.approved_at DESC
             LIMIT 1) AS photo_uuid
      FROM person p
     WHERE p.sign_up_time > NOW() - make_interval(days => %(days)s)
       AND lower(p.email) <> ALL(%(ex)s)
       AND EXISTS (SELECT 1 FROM publishing_queue q
                    JOIN spotlight_revision r ON r.id = q.current_revision_id
                    JOIN spotlight_revision_consent c
                      ON c.revision_id = r.id AND c.person_id = p.id AND c.role = 'subject'
                   WHERE q.subject_person_id = p.id
                     AND q.kind = 'welcome'
                     AND q.status <> 'cancelled')
     ORDER BY p.sign_up_time DESC
"""

# The distinct COUNTRY CODES rather than a count of them: `country` is an
# alpha-2 code, and the roundup's caption and its stored snapshot both face
# the public, so the codes are turned into names in Python (the one mapping
# point, `display_country`) and the distinct count is taken over the NAMES.
# Counting in SQL first would have counted codes, and no name mapping could
# have reached the number afterwards.
_Q_TOTALS = """
    SELECT count(*) AS n,
           array_remove(array_agg(DISTINCT country), NULL) AS country_codes
      FROM person
     WHERE activated
       AND sign_up_time > NOW() - make_interval(days => %(days)s)
       AND lower(email) <> ALL(%(ex)s)
"""


def roundup_snapshot(tx, days: int = 7) -> dict:
    # Lazy import: service.spotlight.queue imports this function at module
    # load time, so a top-level import back the other way would be a cycle
    # (same pattern as revisions.approve_card's lazy settings import).
    from service.spotlight.queue import settings
    tiles = []
    if settings(tx).get('roundup_tiles_enabled') == 'true':
        for r in tx.execute(_Q_CANDIDATES, dict(days=days, ex=_excluded())).fetchall():
            if not r['photo_uuid']:
                continue
            ok, _reason = eligibility(tx, r['id'])
            if not ok:
                continue
            tiles.append(dict(
                person_id=r['id'],
                first_name=r['first_name'],
                photo_url=photo_url(r['photo_uuid'], 450),
                # Carried onto the tile so the revision's participants can
                # name the exact photo the card shows: the dispatch check
                # re-verifies THAT photo, not just any approved one
                # (fix wave item 3).
                photo_uuid=r['photo_uuid'],
            ))
            if len(tiles) == MAX_TILES:
                break
    totals = tx.execute(_Q_TOTALS, dict(days=days, ex=_excluded())).fetchone()
    names = sorted({display_country(c) or c for c in (totals['country_codes'] or []) if c})
    # `countries` keeps its meaning and its type (the number the caption says
    # members joined from); `country_names` is the same set spelled out, so a
    # surface that wants to name them never has to re-resolve the codes.
    return dict(tiles=tiles, count=int(totals['n']),
                countries=len(names), country_names=names)
