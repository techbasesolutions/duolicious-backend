"""Feature occurrences: the cooldown unit for Community Spotlight (Wave 1
remediation F05).

`spotlight_occurrence(kind, person_id, request_key, created_at, UNIQUE
(request_key, person_id))` (Task 1) is the ledger `eligibility` reads for
`featured_recently`. The unit is the REQUEST, not the platform row: a
welcome/member_of_week/highlight card publishes on both facebook and
instagram, and the first platform to confirm must not block the second's
own dispatch check (the F05 bug this module fixes). `person
.spotlight_last_featured_at` stays as a denormalised display column only
(the queue view and the suggest list read it as a quick "last featured"
hint); no eligibility or cooldown check consults it any more.

Every function here runs inside the caller's api_tx; none opens one.
"""
from __future__ import annotations

from service.spotlight.revisions import current_revision


def record_occurrence(tx, kind: str, request_key: str, person_ids: list[int]) -> int:
    """Insert one occurrence per person, ignoring a person already recorded
    for this request_key (the UNIQUE(request_key, person_id) constraint --
    the second platform's confirmation of the same card is a no-op here).
    Returns the number of rows actually inserted."""
    inserted = 0
    for person_id in person_ids:
        cur = tx.execute(
            """INSERT INTO spotlight_occurrence (kind, person_id, request_key)
               VALUES (%(kind)s, %(pid)s, %(rk)s)
               ON CONFLICT (request_key, person_id) DO NOTHING""",
            dict(kind=kind, pid=person_id, rk=request_key))
        inserted += cur.rowcount
    if person_ids:
        # Denormalised display value only (module docstring); kept for the
        # queue view and the suggest list's "last featured" hint. Every
        # person named on this request gets it, whether or not their own
        # occurrence row was new -- a re-confirmation still means the card
        # is freshly live for them.
        tx.execute(
            "UPDATE person SET spotlight_last_featured_at = NOW() WHERE id = ANY(%(ids)s)",
            dict(ids=list(person_ids)))
    return inserted


def pictured_people(tx, queue_row: dict) -> list[int]:
    """Who is actually pictured on this card. welcome/member_of_week/
    highlight name one subject (or none, which should not happen but is
    handled defensively); a roundup's pictured people are whoever the
    CURRENT revision's participants list names -- empty for a count-only
    roundup, since nobody's face is on that card."""
    if queue_row['kind'] == 'roundup':
        rev = current_revision(tx, queue_row['request_key'])
        if not rev:
            return []
        return [p['person_id'] for p in (rev['participants'] or [])]
    subject_id = queue_row.get('subject_person_id')
    return [subject_id] if subject_id is not None else []


def is_first_confirmation(tx, request_key: str) -> bool:
    """True when exactly one row of the request_key has reached status
    'published' -- called right after `record_receipt` has written the
    caller's own row, so a count of 1 means this was the first platform to
    confirm."""
    n = tx.execute(
        "SELECT count(*) AS n FROM publishing_queue WHERE request_key = %(rk)s AND status = 'published'",
        dict(rk=request_key)).fetchone()['n']
    return n == 1
