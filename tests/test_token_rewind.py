"""Discover Rewind (2026-05-19) — POST /tokens/rewind spend path tests.

Mirrors tests/test_token_boost.py: real person rows inserted in a fixture,
cleanup via explicit DELETE. Rewind undoes a PASS (a `skipped` row) for a
token; likes are out of scope.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import credit, get_balance, InsufficientTokens
from service.tokens.actions.rewind import perform as perform_rewind, NothingToRewind


def _make_person(tx):
    return tx.execute(
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
        dict(email=f'rewind-{uuid4()}@example.com'),
    ).fetchone()


@pytest.fixture
def pair():
    """A (me, prospect) pair of real person rows."""
    from database import api_tx
    with api_tx() as tx:
        me = _make_person(tx)
        prospect = _make_person(tx)
    yield {'me': me, 'prospect': prospect}
    with api_tx() as tx:
        tx.execute(
            "DELETE FROM person WHERE id = ANY(%s)",
            ([me['id'], prospect['id']],),
        )


def _skip(tx, me, prospect):
    tx.execute(
        "INSERT INTO skipped (subject_person_id, object_person_id) "
        "VALUES (%(s)s, %(o)s)",
        dict(s=me['id'], o=prospect['id']),
    )


def _skip_exists(tx, me, prospect) -> bool:
    return bool(tx.execute(
        "SELECT 1 FROM skipped WHERE subject_person_id = %(s)s "
        "AND object_person_id = %(o)s",
        dict(s=me['id'], o=prospect['id']),
    ).fetchone())


def test_rewind_debits_1_and_deletes_skip(pair):
    from database import api_tx
    me, prospect = pair['me'], pair['prospect']
    with api_tx() as tx:
        credit(tx, me['uuid'], 5, reason='purchase', metadata={})
        _skip(tx, me, prospect)
        result = perform_rewind(tx, me['uuid'], me['id'], prospect['uuid'])
        assert result == {'rewound': True, 'profile_uuid': prospect['uuid']}
        assert get_balance(tx, me['uuid']) == 4
        assert not _skip_exists(tx, me, prospect)


def test_rewind_with_no_skip_raises_and_does_not_debit(pair):
    from database import api_tx
    me, prospect = pair['me'], pair['prospect']
    with api_tx() as tx:
        credit(tx, me['uuid'], 5, reason='purchase', metadata={})
        with pytest.raises(NothingToRewind):
            perform_rewind(tx, me['uuid'], me['id'], prospect['uuid'])
        # Existence check runs before the debit — balance untouched.
        assert get_balance(tx, me['uuid']) == 5


def test_rewind_insufficient_tokens_keeps_skip(pair):
    from database import api_tx
    me, prospect = pair['me'], pair['prospect']
    with api_tx() as tx:
        _skip(tx, me, prospect)  # balance is 0 — no credit
        with pytest.raises(InsufficientTokens):
            perform_rewind(tx, me['uuid'], me['id'], prospect['uuid'])
        # Debit raises before the deletes, so the skip survives.
        assert _skip_exists(tx, me, prospect)
        assert get_balance(tx, me['uuid']) == 0
