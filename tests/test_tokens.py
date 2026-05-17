"""
Phase 1 Task 1.2 + 1.3 — token ledger module + balance endpoint.

Plan deviation: the plan was written against an async (asyncpg) stack with
`db.fetchval` / `await`, plus fixtures (`db`, `person`, `http`,
`session_token`) that don't exist in this codebase. The ahavah-api codebase
is sync psycopg (`api_tx()` from `database`) with a Flask test `client`.

This test module mirrors the plan's 6 test cases (4 module + 2 endpoint)
but adapts to the actual stack:
  - sync `with api_tx() as tx:` instead of `async db.fetchval`
  - real `person` row created via raw SQL fixture instead of a missing
    `person` fixture
  - Flask test `client` instead of a missing `http`/`session_token`
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import (
    InsufficientTokens,
    credit,
    debit,
    get_balance,
)


@pytest.fixture
def person_uuid():
    """Insert a minimal `person` row and return its UUID. Cleaned up after.

    The migration's FKs are on person(uuid), so we need a real row to credit.
    The `person` table has a lot of NOT NULL columns; we insert only the
    minimum required and rely on cleanup via ON DELETE CASCADE.
    """
    from database import api_tx

    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere'
            )
            RETURNING uuid::text AS uuid, id
            """,
            dict(email=f'tok-{uuid4()}@example.com'),
        ).fetchone()

    yield row

    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


def test_balance_zero_when_no_ledger(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        assert get_balance(tx, person_uuid['uuid']) == 0


def test_credit_increases_balance(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 10, reason='purchase',
               metadata={'stripe_session_id': 'cs_test_xyz'})
        assert get_balance(tx, person_uuid['uuid']) == 10


def test_debit_reduces_balance(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 5, reason='purchase', metadata={})
        debit(tx, person_uuid['uuid'], 2, reason='reveal_liker',
              metadata={'revealed_liker_id': str(uuid4())})
        assert get_balance(tx, person_uuid['uuid']) == 3


def test_debit_raises_when_insufficient(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 1, reason='purchase', metadata={})
        with pytest.raises(InsufficientTokens):
            debit(tx, person_uuid['uuid'], 5, reason='boost', metadata={})
        assert get_balance(tx, person_uuid['uuid']) == 1  # unchanged


# ---------------------------------------------------------------------------
# Task 1.3 — GET /tokens/balance endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def session_token(person_uuid):
    """Create a duo_session row for the test person and return its token."""
    from database import api_tx
    import secrets, hashlib
    tok = secrets.token_urlsafe(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in)
            VALUES (%s, %s, %s, TRUE)
            """,
            (tok_hash, f'tok-session-{person_uuid["id"]}@example.com',
             person_uuid['id']),
        )
    return tok


def test_balance_endpoint_returns_current_value(client, person_uuid, session_token):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 7, reason='purchase', metadata={})
    res = client.get('/tokens/balance',
                     headers={'Authorization': f'Bearer {session_token}'})
    assert res.status_code == 200
    assert res.get_json() == {'balance': 7}


def test_balance_endpoint_requires_auth(client):
    # Plan deviation: missing/malformed bearer token returns 400 in this
    # codebase (service/api/decorators.py:256), not 401 as the plan assumed.
    res = client.get('/tokens/balance')
    assert res.status_code == 400
