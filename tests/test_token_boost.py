"""Phase 7 Task 7.1 — POST /tokens/boost spend path tests.

Mirrors tests/test_tokens.py local-fixture pattern (real person + duo_session
rows inserted in fixtures; cleanup via ON DELETE CASCADE).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import credit, get_balance
from service.tokens.actions.boost import perform as perform_boost


@pytest.fixture
def person_uuid():
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
            dict(email=f'boost-{uuid4()}@example.com'),
        ).fetchone()
    yield row
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (row['id'],))


def test_boost_debits_5_and_inserts_active_boost(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 10, reason='purchase', metadata={})
        result = perform_boost(tx, person_uuid['uuid'])
        assert get_balance(tx, person_uuid['uuid']) == 5
        assert result['boost']['started_at']
        assert result['boost']['expires_at']
        row = tx.execute(
            "SELECT started_at, expires_at FROM active_boosts "
            "WHERE person_id = %(p)s",
            dict(p=person_uuid['uuid']),
        ).fetchone()
        delta = (row['expires_at'] - row['started_at']).total_seconds()
        # 30 minutes ±1 second window
        assert 29 * 60 < delta <= 30 * 60 + 1


def test_boost_replaces_existing_active(person_uuid):
    from database import api_tx
    with api_tx() as tx:
        credit(tx, person_uuid['uuid'], 20, reason='purchase', metadata={})
        perform_boost(tx, person_uuid['uuid'])
        perform_boost(tx, person_uuid['uuid'])
        # Both boosts debit 5 each
        assert get_balance(tx, person_uuid['uuid']) == 10
        # Only one active_boosts row exists per person (UNIQUE on person_id)
        n = tx.execute(
            "SELECT COUNT(*)::int AS n FROM active_boosts "
            "WHERE person_id = %(p)s",
            dict(p=person_uuid['uuid']),
        ).fetchone()['n']
        assert n == 1
