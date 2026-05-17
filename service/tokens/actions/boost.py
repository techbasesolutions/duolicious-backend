"""Spend 5 tokens for a 30-minute profile spotlight in nearby decks.

Plan deviation: the plan's code block was async (asyncpg). The ahavah-api
codebase is sync psycopg via `database.api_tx()`. This module is the sync
translation. Caller owns the transaction so the debit + upsert into
`active_boosts` commit atomically.
"""

from __future__ import annotations

COST = 5

_Q_UPSERT_BOOST = """
  INSERT INTO active_boosts (person_id, started_at, expires_at)
       VALUES (%(person_id)s, NOW(), NOW() + INTERVAL '30 minutes')
  ON CONFLICT (person_id) DO UPDATE
     SET started_at = EXCLUDED.started_at,
         expires_at = EXCLUDED.expires_at
  RETURNING started_at, expires_at
"""


def perform(tx, person_uuid: str) -> dict:
    """Debit 5 tokens and upsert the active_boosts row (30-min spotlight).

    Raises:
        service.tokens.InsufficientTokens if balance < 5. The caller's
        transaction will be rolled back by the surrounding `with api_tx()`.

    Returns:
        {"boost": {"started_at": <iso>, "expires_at": <iso>}}
    """
    from service.tokens import debit

    debit(tx, person_uuid, COST, reason='boost', metadata={})
    row = tx.execute(_Q_UPSERT_BOOST, dict(person_id=person_uuid)).fetchone()
    return {
        'boost': {
            'started_at': row['started_at'].isoformat(),
            'expires_at': row['expires_at'].isoformat(),
        }
    }


_Q_ACTIVE_BOOST = """
  SELECT started_at, expires_at
    FROM active_boosts
   WHERE person_id = %(person_id)s
     AND expires_at > NOW()
"""


def get_active(tx, person_uuid: str) -> dict:
    """Return {"active": bool, "started_at"?: str, "expires_at"?: str}."""
    row = tx.execute(_Q_ACTIVE_BOOST, dict(person_id=person_uuid)).fetchone()
    if not row:
        return {'active': False}
    return {
        'active': True,
        'started_at': row['started_at'].isoformat(),
        'expires_at': row['expires_at'].isoformat(),
    }
