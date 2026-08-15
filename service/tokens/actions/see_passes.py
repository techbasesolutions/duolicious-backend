"""Spend tokens to clear ALL of the caller's own passes at once, bringing
every passed profile back into the deck.

Pass-only + self-only: deletes only the caller's own un-reported skips
and their own search_cache. Never touches likes, matches, reports/blocks,
or any peer's rows -- the opposite of the old bidirectional /decisions/reset.
Caller owns the tx so the debit + deletes commit atomically (rolled back
together if the debit raises InsufficientTokens). reason='rewind' (a bulk
pass-undo) is already allowed by migration 0015.
"""
from __future__ import annotations

from service.tokens import debit

COST = 3


class NothingToSeeAgain(Exception):
    """Raised when the caller has no passes to bring back, so no token is
    spent (mirrors rewind.NothingToRewind -- the check runs before debit)."""


_Q_HAS_PASSES = """
  SELECT 1 FROM skipped
   WHERE subject_person_id = %(p)s AND NOT reported
   LIMIT 1
"""

_Q_CLEAR_SKIPS = """
  DELETE FROM skipped
   WHERE subject_person_id = %(p)s AND NOT reported
"""
_Q_CLEAR_CACHE = "DELETE FROM search_cache WHERE searcher_person_id = %(p)s"


def perform(tx, person_uuid: str, person_id: int) -> dict:
    """Clear the caller's own passes so they re-enter the deck, debiting
    COST tokens. Raises NothingToSeeAgain (before any debit) if the caller
    has no passes to bring back; raises service.tokens.InsufficientTokens
    if they can't afford it. Caller maps these to 409 / 402.
    """
    if not tx.execute(_Q_HAS_PASSES, dict(p=person_id)).fetchone():
        raise NothingToSeeAgain()
    debit(tx, person_uuid, COST, reason='rewind', metadata={'kind': 'see_passes'})
    tx.execute(_Q_CLEAR_SKIPS, dict(p=person_id))
    tx.execute(_Q_CLEAR_CACHE, dict(p=person_id))
    return {'ok': True}
