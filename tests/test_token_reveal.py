"""
Phase 4 Task 4.1 — POST /tokens/reveal endpoint tests.

Plan deviation: the plan was written against an async (asyncpg) stack with
`async http` + `await db.fetchval`. The ahavah-api codebase is sync
psycopg + Flask test client. We mirror the plan's 3 test cases (debits +
records / idempotent / 402-when-insufficient) using the local-fixture
pattern from tests/test_tokens.py (real `person` row + session token via
duo_session insert).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import credit, get_balance


# ---------------------------------------------------------------------------
# Fixtures: real `person` rows + signed-in session token
# ---------------------------------------------------------------------------

def _insert_person():
    """Insert a minimal `person` row, return {uuid, id}. Cascade-cleaned."""
    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly, location_long_friendly, unit_id
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere', 'somewhere, nowhere', (SELECT id FROM unit LIMIT 1)
            )
            RETURNING uuid::text AS uuid, id
            """,
            dict(email=f'reveal-{uuid4()}@example.com'),
        ).fetchone()
    return row


@pytest.fixture
def person():
    row = _insert_person()
    yield row
    from database import api_tx
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


@pytest.fixture
def liker():
    row = _insert_person()
    yield row
    from database import api_tx
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


def _insert_like(liker_id, liked_id):
    """Record that `liker_id` liked `liked_id` so the reveal guard
    (only real likers may be revealed) is satisfied."""
    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            "INSERT INTO liked (liker_id, liked_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (liker_id, liked_id),
        )


@pytest.fixture
def session_token(person):
    """Insert a duo_session row, return the bearer token."""
    from database import api_tx
    import secrets, hashlib
    tok = secrets.token_urlsafe(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%s, %s, %s, TRUE, '123456')
            """,
            (tok_hash, f'reveal-session-{person["id"]}@example.com',
             person['id']),
        )
    return tok


# ---------------------------------------------------------------------------
# Test cases — mirror the plan's 3 scenarios
# ---------------------------------------------------------------------------

def test_reveal_debits_token_and_records_pair(client, person, liker, session_token):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person['uuid'], 3, reason='purchase', metadata={})
    _insert_like(liker['id'], person['id'])

    res = client.post(
        '/tokens/reveal',
        json={'liker_id': liker['uuid']},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 200
    assert res.get_json() == {'revealed': True}

    with api_tx() as tx:
        assert get_balance(tx, person['uuid']) == 2
        row = tx.execute(
            """SELECT 1 AS ok FROM revealed_likers
                WHERE viewer_id = %(v)s AND liker_id = %(l)s""",
            dict(v=person['uuid'], l=liker['uuid']),
        ).fetchone()
        assert row is not None


def test_reveal_is_idempotent_no_double_debit(client, person, liker, session_token):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person['uuid'], 3, reason='purchase', metadata={})
    _insert_like(liker['id'], person['id'])

    headers = {'Authorization': f'Bearer {session_token}'}
    body = {'liker_id': liker['uuid']}
    client.post('/tokens/reveal', json=body, headers=headers)
    client.post('/tokens/reveal', json=body, headers=headers)

    with api_tx() as tx:
        # Second call must NOT double-debit — still 2.
        assert get_balance(tx, person['uuid']) == 2


def test_reveal_402_when_insufficient(client, person, liker, session_token):
    _insert_like(liker['id'], person['id'])
    res = client.post(
        '/tokens/reveal',
        json={'liker_id': liker['uuid']},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 402
    assert res.get_json() == {'error': 'insufficient_tokens'}


def test_reveal_400_when_not_a_liker(client, person, liker, session_token):
    """A liker_id that never liked the viewer is rejected without debiting —
    guards against a crafted UUID burning a token on a no-op reveal."""
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person['uuid'], 3, reason='purchase', metadata={})
    # Note: no _insert_like — `liker` never liked `person`.

    res = client.post(
        '/tokens/reveal',
        json={'liker_id': liker['uuid']},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 400
    assert res.get_json() == {'error': 'not_a_liker'}

    with api_tx() as tx:
        # No token spent.
        assert get_balance(tx, person['uuid']) == 3
