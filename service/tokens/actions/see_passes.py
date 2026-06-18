"""Spend tokens to clear ALL of the caller's own passes at once, bringing
every passed profile back into the deck.

Pass-only + self-only: deletes only the caller's own un-reported skips,
their own 'pass' swipes, and their own search_cache. Never touches likes,
matches, reports/blocks, or any peer's rows -- the opposite of the old
bidirectional /decisions/reset. Caller owns the tx so the debit + deletes
commit atomically (rolled back together if the debit raises
InsufficientTokens). reason='rewind' (a bulk pass-undo) is already allowed
by migration 0015.
"""
from __future__ import annotations

from service.tokens import debit

COST = 3

_Q_CLEAR_SKIPS = """
  DELETE FROM skipped
   WHERE subject_person_id = %(p)s AND NOT reported
"""
_Q_CLEAR_PASS_SWIPES = """
  DELETE FROM swipe
   WHERE swiper_person_id = %(p)s AND direction = 'pass'
"""
_Q_CLEAR_CACHE = "DELETE FROM search_cache WHERE searcher_person_id = %(p)s"


def perform(tx, person_uuid: str, person_id: int) -> dict:
    """Debit COST tokens, then clear the caller's own passes so they
    re-enter the deck. Raises service.tokens.InsufficientTokens if the
    caller can't afford it (the caller maps that to 402)."""
    debit(tx, person_uuid, COST, reason='rewind', metadata={'kind': 'see_passes'})
    tx.execute(_Q_CLEAR_SKIPS, dict(p=person_id))
    tx.execute(_Q_CLEAR_PASS_SWIPES, dict(p=person_id))
    tx.execute(_Q_CLEAR_CACHE, dict(p=person_id))
    return {'ok': True}
