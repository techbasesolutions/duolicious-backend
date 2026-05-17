"""
Phase 4 Task 4.2 — /likes/incoming visibility after reveal.

Plan deviation: the plan was written against an async http/db. The
ahavah-api codebase is sync Flask test client + sync psycopg. We adapt
the plan's `test_incoming_likes_includes_revealed_photos_for_non_premium`
case to use the local-fixture pattern from tests/test_token_reveal.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from service.tokens import credit


def _insert_person():
    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly,
                activated
            )
            VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1),
                'about', 'somewhere', TRUE
            )
            RETURNING uuid::text AS uuid, id
            """,
            dict(email=f'inc-{uuid4()}@example.com'),
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
            (tok_hash, f'inc-session-{person["id"]}@example.com',
             person['id']),
        )
    return tok


def test_incoming_likes_includes_revealed_photos_for_non_premium(
    client, person, liker, session_token,
):
    """Pre-reveal: count==1, likes==[]. Post-reveal: that liker appears."""
    from database import api_tx
    # The liker has liked `person`. Insert raw to avoid round-tripping
    # via POST /decisions (which would also create no match since the
    # reverse hasn't fired).
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO liked (liker_id, liked_id)
                    VALUES (%(liker)s, %(liked)s)""",
            dict(liker=liker['id'], liked=person['id']),
        )

    headers = {'Authorization': f'Bearer {session_token}'}

    # Pre-reveal — count==1; the liker appears as a HIDDEN stub
    # (id only, no name/age/photo) so the frontend can render a
    # tappable blurred card per real liker. Plan deviation: the
    # plan spec asserted `likes == []` here, but the wired UX in
    # Task 4.4 needs per-liker IDs to call POST /tokens/reveal;
    # withholding the photo fields keeps the paywall intact.
    res = client.get('/likes/incoming', headers=headers)
    assert res.status_code == 200
    body = res.get_json()
    assert body['count'] == 1
    assert body['premium'] is False
    assert len(body['likes']) == 1
    pre = body['likes'][0]
    assert pre['hidden'] is True
    assert pre['with_profile']['id'] == liker['uuid']
    # The paywalled fields stay server-side.
    assert 'firstName' not in pre['with_profile']
    assert 'photo_uuids' not in pre['with_profile']

    # Credit tokens + reveal — that liker now appears with full fields.
    with api_tx() as tx:
        credit(tx, person['uuid'], 3, reason='purchase', metadata={})

    res = client.post(
        '/tokens/reveal',
        json={'liker_id': liker['uuid']},
        headers=headers,
    )
    assert res.status_code == 200

    res = client.get('/likes/incoming', headers=headers)
    body = res.get_json()
    assert body['count'] == 1
    assert body['premium'] is False
    assert len(body['likes']) == 1
    post = body['likes'][0]
    assert post['hidden'] is False
    assert post['with_profile']['id'] == liker['uuid']
    assert 'firstName' in post['with_profile']
