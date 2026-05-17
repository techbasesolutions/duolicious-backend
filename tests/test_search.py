"""Phase 7 Task 7.2 — boosted candidates surface first in /search.

The discovery query (Q_UNCACHED_SEARCH_2 in service/search/sql/__init__.py)
LEFT JOINs `active_boosts` and prepends `is_boosted DESC` to the cache-position
ROW_NUMBER ORDER BY. This test inserts a searcher + a small candidate pool,
boosts one of the candidates, runs /search, and asserts the boosted UUID is
first in the result list.

Uses local-fixture pattern from tests/test_tokens.py.
"""

from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4

import pytest


def _insert_person(tx, *, email_prefix='search'):
    return tx.execute(
        """
        INSERT INTO person (
            email, normalized_email, name, date_of_birth,
            coordinates, gender_id, about, location_short_friendly,
            activated, last_online_time
        )
        VALUES (
            %(email)s, %(email)s, 'Test', '1990-01-01',
            ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
            (SELECT id FROM gender LIMIT 1),
            'about', 'somewhere',
            TRUE, NOW()
        )
        RETURNING uuid::text AS uuid, id
        """,
        dict(email=f'{email_prefix}-{uuid4()}@example.com'),
    ).fetchone()


@pytest.fixture
def searcher_and_candidates():
    """Insert 1 searcher + 4 candidate persons; clean up on teardown."""
    from database import api_tx
    with api_tx() as tx:
        searcher = _insert_person(tx, email_prefix='search-searcher')
        candidates = [
            _insert_person(tx, email_prefix=f'search-cand-{i}')
            for i in range(4)
        ]
    yield {'searcher': searcher, 'candidates': candidates}
    with api_tx() as tx:
        ids = [searcher['id']] + [c['id'] for c in candidates]
        tx.execute(
            "DELETE FROM person WHERE id = ANY(%(ids)s::int[])",
            dict(ids=ids),
        )


@pytest.fixture
def session_token(searcher_and_candidates):
    tok = secrets.token_urlsafe(32)
    tok_hash = hashlib.sha512(tok.encode()).hexdigest()
    sid = searcher_and_candidates['searcher']['id']
    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in)
            VALUES (%s, %s, %s, TRUE)
            """,
            (tok_hash, f'search-session-{sid}@example.com', sid),
        )
    return tok


def test_search_orders_boosted_first(client, searcher_and_candidates, session_token):
    """A boosted candidate should appear at position 0 of /search results."""
    from database import api_tx
    boosted = searcher_and_candidates['candidates'][3]
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO active_boosts (person_id, started_at, expires_at)
            VALUES (%(p)s, NOW(), NOW() + INTERVAL '30 minutes')
            """,
            dict(p=boosted['uuid']),
        )
    res = client.get(
        '/search?n=10&o=0',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    assert res.status_code == 200
    body = res.get_json()
    # Result list shape depends on the /search response wrapper; the
    # boosted candidate's UUID must be the first one returned.
    items = body if isinstance(body, list) else (
        body.get('results') or body.get('candidates') or body.get('prospects') or []
    )
    assert items, f'/search returned empty body: {body!r}'
    first = items[0]
    first_uuid = first.get('prospect_uuid') or first.get('uuid') or first.get('id')
    assert str(first_uuid) == str(boosted['uuid']), (
        f'expected boosted candidate {boosted["uuid"]} first, got {first_uuid}; '
        f'full first item: {first!r}'
    )
