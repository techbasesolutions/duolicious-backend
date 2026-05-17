"""Spend 3 tokens for a 24-hour bypass of the daily like quota.

Plan deviation: the plan was written for an async (asyncpg) stack with
`async def perform(db, ...)` and `async with db.transaction()`. The
ahavah-api codebase is sync psycopg — `with api_tx() as tx:` is owned by
the CALLER (route handler), so this `perform` takes a cursor `tx`.
Atomicity is preserved because the caller wraps the call in one api_tx.

The day-pass is a single ledger row with `reason='day_pass'` and
`metadata.expires_at` 24h in the future. `service.decisions._check_like_quota`
reads the row via `(metadata->>'expires_at')::timestamptz > NOW()`; no
denormalized expiry column is needed.
"""
from datetime import datetime, timedelta, timezone
from typing import Union
from uuid import UUID

from service.tokens import debit


COST = 3
DURATION = timedelta(hours=24)


def perform(tx, person_uuid: Union[UUID, str]) -> dict:
    """Spend 3 tokens to unlock 24h of unlimited likes.

    Raises `service.tokens.InsufficientTokens` if balance < 3; the
    caller is expected to translate that to 402.

    Returns `{"day_pass": {"expires_at": <iso>}}`.
    """
    expires = (datetime.now(tz=timezone.utc) + DURATION).isoformat()
    debit(
        tx, str(person_uuid), COST,
        reason='day_pass',
        metadata={'expires_at': expires},
    )
    return {"day_pass": {"expires_at": expires}}
