"""
Token ledger — the single source of truth for per-user token balance.

Balance is always SUM(delta) over token_ledger for the user. No
denormalized counter. Spends are wrapped in a FOR UPDATE transaction
to prevent double-spend on concurrent requests.

Plan deviation: the plan specified an async (asyncpg) interface
(`await db.fetchval(...)`). The ahavah-api codebase is sync psycopg
(`database.api_tx()` -> psycopg cursor). This module exposes the same
public surface (`get_balance`, `credit`, `debit`, `InsufficientTokens`)
but synchronously, taking a psycopg cursor `tx` as the first arg.
The caller owns the transaction (so a debit + downstream write —
revealed_likers, active_boosts, liked.is_super — commit atomically).
"""
import json
from typing import Any
from service.tokens.sql import (
    Q_BALANCE,
    Q_HISTORY,
    Q_INSERT_LEDGER,
)


# Per-user advisory lock acquired inside the caller's transaction. Replaces
# the prior Q_BALANCE_FOR_UPDATE pattern (`SELECT SUM(...) FOR UPDATE`),
# which Postgres rejects with `FOR UPDATE is not allowed with aggregate
# functions`. The advisory lock is keyed off hashtext(uuid) so concurrent
# spends by the same user serialize, while different users don't contend.
# Released automatically on commit/rollback (xact-scoped).
_Q_LOCK_USER = "SELECT pg_advisory_xact_lock(hashtext(%(person_id)s))"


class InsufficientTokens(Exception):
    """Raised when a debit would drive the balance negative."""


def get_balance(tx, person_uuid: str) -> int:
    """Current token balance for the user. Cheap (single SUM)."""
    row = tx.execute(Q_BALANCE, dict(person_id=person_uuid)).fetchone()
    return int(row['balance']) if row else 0


def get_history(tx, person_uuid: str, *, limit: int, offset: int) -> list:
    """Raw ledger rows for the user, newest first (id, delta, reason,
    metadata, created_at). The route owns response shaping (labels, money)."""
    return tx.execute(
        Q_HISTORY,
        dict(person_id=person_uuid, limit=limit, offset=offset),
    ).fetchall()


def credit(tx, person_uuid: str, amount: int, *,
           reason: str, metadata: dict[str, Any]) -> None:
    """Append a positive ledger row (purchase, stipend, refund-reverse)."""
    if amount <= 0:
        raise ValueError(f"credit amount must be positive, got {amount}")
    tx.execute(
        Q_INSERT_LEDGER,
        dict(
            person_id=person_uuid,
            delta=amount,
            reason=reason,
            metadata=json.dumps(metadata),
        ),
    )


def debit(tx, person_uuid: str, amount: int, *,
          reason: str, metadata: dict[str, Any]) -> None:
    """Append a negative ledger row inside a FOR UPDATE transaction.

    Raises InsufficientTokens if balance < amount. The transaction must
    be held open by the CALLER so subsequent writes (revealed_likers,
    active_boosts, liked.is_super, etc.) are committed atomically with
    the debit.
    """
    if amount <= 0:
        raise ValueError(f"debit amount must be positive, got {amount}")
    tx.execute(_Q_LOCK_USER, dict(person_id=str(person_uuid)))
    row = tx.execute(
        Q_BALANCE, dict(person_id=person_uuid)
    ).fetchone()
    balance = int(row['balance']) if row else 0
    if balance < amount:
        raise InsufficientTokens(f"have {balance}, need {amount}")
    tx.execute(
        Q_INSERT_LEDGER,
        dict(
            person_id=person_uuid,
            delta=-amount,
            reason=reason,
            metadata=json.dumps(metadata),
        ),
    )
