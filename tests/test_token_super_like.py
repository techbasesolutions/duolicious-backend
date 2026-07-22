"""Phase 6 Task 6.1 — POST /tokens/super-like.

Plan deviation: the plan tests were async + assumed `db`, `person`,
`candidate`, `http`, `session_token` fixtures that don't exist. We
adapt to the local-fixture pattern (tests/test_tokens.py +
tests/test_decisions.py): insert real person rows in the test, sync
`api_tx`, sync Flask test client.

Also: the `liked` table keys on INT person.id (NOT uuid — see
0006_match_loop.sql), so the test asserts is_super by joining via
person.uuid → person.id.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import credit, get_balance


def _insert_person():
    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly, location_long_friendly, unit_id,
                activated
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere', 'somewhere, nowhere', (SELECT id FROM unit LIMIT 1), TRUE
            )
            RETURNING uuid::text AS uuid, id
            """,
            dict(email=f'sl-{uuid4()}@example.com'),
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
def candidate():
    row = _insert_person()
    yield row
    from database import api_tx
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


@pytest.fixture
def session_token(person):
    from database import api_tx
    import secrets, hashlib
    tok = secrets.token_hex(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%s, %s, %s, TRUE, '123456')
            """,
            (tok_hash, f'sl-session-{person["id"]}@example.com',
             person['id']),
        )
    return tok


def test_super_like_debits_2_and_sets_is_super(
    client, person, candidate, session_token,
):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person['uuid'], 5, reason='purchase', metadata={})

    res = client.post(
        '/tokens/super-like',
        json={'person_id': candidate['uuid']},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body['super_liked'] is True
    assert body['match_id'] is None  # candidate hasn't liked back

    with api_tx() as tx:
        assert get_balance(tx, person['uuid']) == 3
        row = tx.execute(
            """SELECT is_super FROM liked
                WHERE liker_id = %(liker)s AND liked_id = %(liked)s""",
            dict(liker=person['id'], liked=candidate['id']),
        ).fetchone()
    assert row is not None
    assert row['is_super'] is True


def test_super_like_402_when_insufficient(
    client, person, candidate, session_token,
):
    # Zero balance; super-like costs 2.
    res = client.post(
        '/tokens/super-like',
        json={'person_id': candidate['uuid']},
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 402
    body = res.get_json()
    assert body['error'] == 'insufficient_tokens'

    from database import api_tx
    with api_tx() as tx:
        # No liked row should have been written.
        row = tx.execute(
            """SELECT 1 FROM liked
                WHERE liker_id = %(liker)s AND liked_id = %(liked)s""",
            dict(liker=person['id'], liked=candidate['id']),
        ).fetchone()
    assert row is None
