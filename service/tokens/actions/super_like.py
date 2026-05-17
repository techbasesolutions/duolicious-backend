"""Spend 2 tokens to send a super-like.

Plan deviation: the plan's snippet was written against an async (asyncpg)
stack with `$1`/`$2` placeholders, and treated viewer/target as `uuid`
arguments to the `liked` table. The `liked` table on this codebase
(migrations/0006_match_loop.sql) keys on INT `person.id`, NOT uuid —
same situation as the Phase 5 quota check. So this `perform` accepts
uuids (matching the token_ledger uuid convention) and resolves them to
person.id internally before writing `liked`.

Also: `liked`'s primary key is the composite (liker_id, liked_id) — no
synthetic `id` column to RETURN. The plan's `RETURNING id` was therefore
unusable; instead we use the resulting `ahavah_match.match_id` when a
mutual match forms (or null when the target hasn't liked back yet).
"""

from uuid import UUID
from typing import Union

from service.tokens import debit


COST = 2


_Q_RESOLVE_UUID_TO_ID = """
  SELECT id FROM person WHERE uuid = %(uuid)s::uuid
"""


_Q_UPSERT_SUPER_LIKE = """
  INSERT INTO liked (liker_id, liked_id, is_super)
       VALUES (%(liker_id)s, %(liked_id)s, TRUE)
  ON CONFLICT (liker_id, liked_id) DO UPDATE SET is_super = TRUE
"""


_Q_RECIPROCAL_EXISTS = """
  SELECT 1 AS ok FROM liked
   WHERE liker_id = %(target_id)s AND liked_id = %(viewer_id)s
"""


_Q_UPSERT_MATCH = """
  INSERT INTO ahavah_match (user_a_id, user_b_id)
       VALUES (LEAST(%(a)s, %(b)s), GREATEST(%(a)s, %(b)s))
  ON CONFLICT (user_a_id, user_b_id) DO NOTHING
  RETURNING match_id::text AS match_id
"""


_Q_FETCH_MATCH = """
  SELECT match_id::text AS match_id FROM ahavah_match
   WHERE user_a_id = LEAST(%(a)s, %(b)s)
     AND user_b_id = GREATEST(%(a)s, %(b)s)
"""


def perform(tx, viewer_uuid: Union[UUID, str], target_uuid: Union[UUID, str]) -> dict:
    """Spend 2 tokens to send a super-like.

    Debits 2 tokens with reason='super_like', then upserts the
    `liked` row with is_super=TRUE. If the target had previously
    liked the viewer (mutual), also upserts an ahavah_match row and
    returns its match_id. Otherwise match_id is null.

    Raises `service.tokens.InsufficientTokens` if the viewer can't
    afford the debit; the caller translates that to 402.

    The caller owns the transaction via `with api_tx() as tx:` so the
    debit, liked-upsert, and match-upsert commit atomically.
    """
    debit(
        tx, str(viewer_uuid), COST,
        reason='super_like',
        metadata={'super_liked_person_id': str(target_uuid)},
    )

    # Resolve uuids → person.id (liked table keys on INT).
    viewer_row = tx.execute(
        _Q_RESOLVE_UUID_TO_ID, dict(uuid=str(viewer_uuid))
    ).fetchone()
    target_row = tx.execute(
        _Q_RESOLVE_UUID_TO_ID, dict(uuid=str(target_uuid))
    ).fetchone()
    if not viewer_row or not target_row:
        # Defensive — debit already happened, but if the target id is
        # bogus we still want to avoid writing a half-baked liked row.
        # The api_tx will roll the debit back on the raise.
        raise ValueError("unknown person uuid")
    viewer_id = viewer_row['id']
    target_id = target_row['id']

    tx.execute(
        _Q_UPSERT_SUPER_LIKE,
        dict(liker_id=viewer_id, liked_id=target_id),
    )

    reciprocal = tx.execute(
        _Q_RECIPROCAL_EXISTS,
        dict(viewer_id=viewer_id, target_id=target_id),
    ).fetchone()

    if not reciprocal:
        return {"super_liked": True, "match_id": None}

    inserted = tx.execute(
        _Q_UPSERT_MATCH,
        dict(a=viewer_id, b=target_id),
    ).fetchone()
    if inserted and inserted.get('match_id'):
        return {"super_liked": True, "match_id": inserted['match_id']}

    # Match row already existed (idempotent re-tap on a mutual we'd
    # previously formed); fetch its id.
    existing = tx.execute(
        _Q_FETCH_MATCH, dict(a=viewer_id, b=target_id),
    ).fetchone()
    return {
        "super_liked": True,
        "match_id": existing['match_id'] if existing else None,
    }
