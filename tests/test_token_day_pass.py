"""Phase 5 Task 5.2 — POST /tokens/day-pass endpoint tests.

Plan deviation: the plan was written against an async stack with
`async http` + `await db.fetchrow`. We adapt to the sync Flask test
`client` + the local-fixture pattern from tests/test_token_reveal.py
(real `person` row + signed-in session token).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from service.tokens import credit, get_balance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _insert_person():
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
            dict(email=f'dp-{uuid4()}@example.com'),
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
def session_token(person):
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
            (tok_hash, f'dp-session-{person["id"]}@example.com',
             person['id']),
        )
    return tok


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_day_pass_debits_3_and_writes_expires_at(client, person, session_token):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person['uuid'], 5, reason='purchase', metadata={})

    res = client.post(
        '/tokens/day-pass',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert 'day_pass' in body
    assert 'expires_at' in body['day_pass']

    with api_tx() as tx:
        assert get_balance(tx, person['uuid']) == 2
        row = tx.execute(
            """SELECT metadata->>'expires_at' AS exp FROM token_ledger
                WHERE person_id = %(p)s AND reason = 'day_pass'
                ORDER BY created_at DESC LIMIT 1""",
            dict(p=person['uuid']),
        ).fetchone()
    assert row is not None
    exp = datetime.fromisoformat(row['exp'])
    delta = (exp - datetime.now(tz=timezone.utc)).total_seconds()
    # Within (23h, 24h] window — allows for a tiny clock skew during the call.
    assert 23 * 3600 < delta <= 24 * 3600 + 5


def test_day_pass_402_when_insufficient(client, person, session_token):
    res = client.post(
        '/tokens/day-pass',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 402
    assert res.get_json() == {'error': 'insufficient_tokens'}
