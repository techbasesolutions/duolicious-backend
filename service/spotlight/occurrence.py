"""Feature occurrences: the cooldown unit for Community Spotlight (Wave 1
remediation F05).

`spotlight_occurrence(kind, person_id, request_key, created_at, UNIQUE
(request_key, person_id))` (Task 1) is the ledger `eligibility` reads for
`featured_recently`. The unit is the REQUEST, not the platform row: a
welcome/member_of_week/highlight card publishes on both facebook and
instagram, and the first platform to confirm must not block the second's
own dispatch check (the F05 bug this module fixes). `person
.spotlight_last_featured_at` is write-only from here on: `record_occurrence`
below still stamps it as a denormalised display hint for whatever surface
might want a cheap "last featured" column someday, but nothing in this
codebase reads it back any more -- the queue view has no such column, and
the suggest list's "last featured" ordering and reason both aggregate
`spotlight_occurrence` directly (`service/api/admin/spotlight_routes.py`'s
`_Q_LAST_FEATURED_GENDER`/`_Q_SUGGEST`). No eligibility or cooldown check
consults the person column either.

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
    confirm.

    Two sibling platform completions (facebook's own `/complete` call and
    instagram's own) each run this in a SEPARATE `api_tx`, but `record_receipt`'s
    `UPDATE ... status = 'published'` and `record_occurrence`'s writes (the
    unique insert into `spotlight_occurrence` and the `person` UPDATE) commit
    as part of THIS same transaction before this count runs -- and `api_tx`
    holds one shared connection behind a single lock (`database.py`), so no
    second sibling's transaction can even begin until this one has fully
    committed. There is therefore no window in which a later cleanup, or the
    other sibling's own completion, could observe or drop a half-written
    occurrence row before this count reads it: by the time either happens,
    this transaction's writes are either fully committed or have not started."""
    n = tx.execute(
        "SELECT count(*) AS n FROM publishing_queue WHERE request_key = %(rk)s AND status = 'published'",
        dict(rk=request_key)).fetchone()['n']
    return n == 1
