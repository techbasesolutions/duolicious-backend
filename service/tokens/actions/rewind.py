"""Spend 1 token to undo the last pass (skip) and re-show a profile.

Scope: passes only. Likes are not rewindable (a like may have created a
match + sent a push). The /profile/tokens copy says "Undo your last pass
and reconsider." Caller owns the transaction so the debit + the skip
deletion commit atomically (rolled back together if the debit raises
InsufficientTokens).
"""

from __future__ import annotations

COST = 1


class NothingToRewind(Exception):
    """Raised when there is no skip to undo for (me, prospect)."""


_Q_SKIP_EXISTS = """
  SELECT 1
    FROM skipped s
   WHERE s.subject_person_id = %(me_id)s
     AND s.object_person_id = (
       SELECT id FROM person WHERE uuid = uuid_or_null(%(prospect_uuid)s)
     )
   LIMIT 1
"""

_Q_DELETE_SKIP = """
  DELETE FROM skipped
   WHERE subject_person_id = %(me_id)s
     AND object_person_id = (
       SELECT id FROM person WHERE uuid = uuid_or_null(%(prospect_uuid)s)
     )
"""

# The paid-back profile must reappear inside the CURRENT cached deck
# session, not wait for the next /search rebuild: see-passes and
# take-back-like both already clear their pair's search_cache row for
# the same reason.
_Q_DELETE_CACHE_PAIR = """
  DELETE FROM search_cache
   WHERE searcher_person_id = %(me_id)s
     AND prospect_person_id = (
       SELECT id FROM person WHERE uuid = uuid_or_null(%(prospect_uuid)s)
     )
"""


def perform(tx, person_uuid: str, person_id: int, prospect_uuid: str) -> dict:
    """Debit COST tokens and delete the skip for the prospect.

    Raises:
        NothingToRewind  if no skip exists for (me, prospect) — caller maps
                         to 409 and does NOT debit (the existence check runs
                         before the debit).
        service.tokens.InsufficientTokens if balance < COST.

    Returns:
        {"rewound": True, "profile_uuid": <uuid>}
    """
    from service.tokens import debit

    if not tx.execute(
        _Q_SKIP_EXISTS, dict(me_id=person_id, prospect_uuid=prospect_uuid)
    ).fetchone():
        raise NothingToRewind()

    debit(tx, person_uuid, COST, reason='rewind',
          metadata={'prospect': prospect_uuid})
    tx.execute(_Q_DELETE_SKIP, dict(me_id=person_id, prospect_uuid=prospect_uuid))
    tx.execute(_Q_DELETE_CACHE_PAIR, dict(me_id=person_id, prospect_uuid=prospect_uuid))
    return {'rewound': True, 'profile_uuid': prospect_uuid}
