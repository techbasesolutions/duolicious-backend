"""Reveal a hidden liker for 1 token (idempotent per viewer/liker pair).

Plan deviation: the plan was written against an async (asyncpg) stack
with `async with db.transaction()` and `$1`/`$2` placeholders. The
ahavah-api codebase is sync psycopg — `with api_tx() as tx:` is owned by
the CALLER, so this `perform` takes a cursor `tx` and uses `%(name)s`
placeholders. Atomicity of debit + reveal-insert is preserved because
the caller wraps both inside one api_tx.
"""

from uuid import UUID
from typing import Union

from service.tokens import debit


COST = 1


class NotALiker(Exception):
    """Raised when the target hasn't actually liked the viewer — guards
    against a crafted liker_id debiting a token for a no-op reveal."""


_Q_ALREADY_REVEALED = """
  SELECT 1 FROM revealed_likers
   WHERE viewer_id = %(viewer_id)s AND liker_id = %(liker_id)s
"""

_Q_IS_REAL_LIKER = """
  SELECT 1 FROM liked l
    JOIN person liker  ON liker.id  = l.liker_id
    JOIN person viewer ON viewer.id = l.liked_id
   WHERE liker.uuid  = %(liker_uuid)s::uuid
     AND viewer.uuid = %(viewer_uuid)s::uuid
   LIMIT 1
"""

_Q_INSERT_REVEAL = """
  INSERT INTO revealed_likers (viewer_id, liker_id)
       VALUES (%(viewer_id)s, %(liker_id)s)
       ON CONFLICT DO NOTHING
"""


def perform(tx, viewer_uuid: Union[UUID, str], liker_uuid: Union[UUID, str]) -> dict:
    """Spend 1 token to reveal a liker.

    Idempotent: if already revealed, no debit + no error. Returns
    {"revealed": True}. Raises `service.tokens.InsufficientTokens` if
    the viewer can't afford the debit; the caller is expected to
    translate that to 402.

    The caller owns the transaction via `with api_tx() as tx:` so the
    debit and `revealed_likers` insert commit atomically.

    Raises `NotALiker` if `liker_uuid` never liked the viewer.
    """
    # Serialize the whole check-debit-insert per viewer so a double-tap
    # can't pass the already-revealed check twice and double-debit. Same
    # advisory-lock key debit() acquires (hashtext of the viewer uuid), so
    # it nests harmlessly with debit's own lock.
    tx.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%(v)s))",
        dict(v=str(viewer_uuid)),
    )

    already = tx.execute(
        _Q_ALREADY_REVEALED,
        dict(viewer_id=str(viewer_uuid), liker_id=str(liker_uuid)),
    ).fetchone()
    if already:
        return {"revealed": True}

    real = tx.execute(
        _Q_IS_REAL_LIKER,
        dict(viewer_uuid=str(viewer_uuid), liker_uuid=str(liker_uuid)),
    ).fetchone()
    if not real:
        raise NotALiker()

    debit(
        tx, str(viewer_uuid), COST,
        reason='reveal_liker',
        metadata={'revealed_liker_id': str(liker_uuid)},
    )
    tx.execute(
        _Q_INSERT_REVEAL,
        dict(viewer_id=str(viewer_uuid), liker_id=str(liker_uuid)),
    )
    return {"revealed": True}
