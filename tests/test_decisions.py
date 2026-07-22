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
            INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
            VALUES (%s, %s, %s, TRUE, '123456')
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


# ---------------------------------------------------------------------------
# Phase 5 — daily like-quota enforcement (10/day, premium + day-pass bypass).
#
# Plan deviation: the plan's quota tests used an async http + `candidates`
# fixture (11 prefab candidate UUIDs) that don't exist here. We adapt to
# the sync Flask client + local-fixture pattern by inserting 11 candidate
# `person` rows in the test itself, then driving POST /decisions for each.
# ---------------------------------------------------------------------------

def _make_candidates(n):
    """Insert n minimal candidate person rows, return their {uuid,id} dicts.

    Cleanup happens via the test's manual DELETE at the end (kept inline
    rather than a fixture so each test owns its own candidate pool size).
    """
    return [_insert_person() for _ in range(n)]


def _cleanup_candidates(candidates):
    from database import api_tx
    ids = [c['id'] for c in candidates]
    if not ids:
        return
    with api_tx() as tx:
        tx.execute(
            "DELETE FROM person WHERE id = ANY(%(ids)s)",
            dict(ids=ids),
        )


def test_decisions_quota_blocks_free_user_after_10(client, person, session_token):
    """Free user — first 10 likes return 200; 11th returns 429 + resets_at."""
    candidates = _make_candidates(11)
    try:
        headers = {'Authorization': f'Bearer {session_token}'}
        for c in candidates[:10]:
            res = client.post(
                '/decisions',
                json={'profile_uuid': c['uuid'], 'decision': 'like'},
                headers=headers,
            )
            assert res.status_code == 200, res.get_data(as_text=True)

        res = client.post(
            '/decisions',
            json={'profile_uuid': candidates[10]['uuid'], 'decision': 'like'},
            headers=headers,
        )
        assert res.status_code == 429
        body = res.get_json()
        assert body['error'] == 'quota_exceeded'
        assert 'resets_at' in body
    finally:
        _cleanup_candidates(candidates)


def test_decisions_quota_bypassed_for_premium(client, person, session_token):
    """Premium entitlement bypasses the cap — all 11 likes succeed."""
    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            "UPDATE person SET entitlements = ARRAY['premium'] WHERE id = %(id)s",
            dict(id=person['id']),
        )
    candidates = _make_candidates(11)
    try:
        headers = {'Authorization': f'Bearer {session_token}'}
        for c in candidates:
            res = client.post(
                '/decisions',
                json={'profile_uuid': c['uuid'], 'decision': 'like'},
                headers=headers,
            )
            assert res.status_code == 200, res.get_data(as_text=True)
    finally:
        _cleanup_candidates(candidates)


# ---------------------------------------------------------------------------
# Phase 6 — /likes/incoming sorts is_super=TRUE first + exposes is_super flag
# ---------------------------------------------------------------------------

def test_incoming_likes_super_first_and_flagged(client, person, session_token):
    """Super-likes sort above plain likes; is_super flag exposed per record."""
    from database import api_tx

    # Two distinct likers — one super-like, one plain like. Both
    # inserted explicitly so we control ordering: the plain like is
    # MORE RECENT (created_at later), so without the is_super-first
    # ORDER BY it would come first. Phase 6's sort puts super first
    # regardless of recency.
    liker_plain = _insert_person()
    liker_super = _insert_person()
    try:
        # Promote viewer to premium so both likes are visible (avoids
        # the reveal-paywall hiding the plain like and skewing the test).
        with api_tx() as tx:
            tx.execute(
                "UPDATE person SET entitlements = ARRAY['premium'] WHERE id = %(id)s",
                dict(id=person['id']),
            )
            # Super-like FIRST (older), plain like SECOND (newer) — so
            # is_super sort wins over created_at sort.
            tx.execute(
                """INSERT INTO liked (liker_id, liked_id, is_super, created_at)
                   VALUES (%(liker)s, %(liked)s, TRUE, NOW() - INTERVAL '1 hour')""",
                dict(liker=liker_super['id'], liked=person['id']),
            )
            tx.execute(
                """INSERT INTO liked (liker_id, liked_id, is_super, created_at)
                   VALUES (%(liker)s, %(liked)s, FALSE, NOW())""",
                dict(liker=liker_plain['id'], liked=person['id']),
            )

        res = client.get(
            '/likes/incoming',
            headers={'Authorization': f'Bearer {session_token}'},
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        body = res.get_json()
        assert body['count'] == 2
        assert len(body['likes']) == 2

        # Super-liker first despite older created_at.
        assert body['likes'][0]['with_profile']['id'] == liker_super['uuid']
        assert body['likes'][0]['is_super'] is True
        assert body['likes'][1]['with_profile']['id'] == liker_plain['uuid']
        assert body['likes'][1]['is_super'] is False
    finally:
        _cleanup_candidates([liker_plain, liker_super])


def test_decisions_quota_bypassed_with_active_day_pass(client, person, session_token):
    """An unexpired day_pass ledger row bypasses the cap."""
    from datetime import datetime, timedelta, timezone
    from database import api_tx
    expires = (datetime.now(tz=timezone.utc) + timedelta(hours=24)).isoformat()
    with api_tx() as tx:
        # Credit balance + write a day_pass debit. credit/debit aren't
        # used here — we INSERT raw so the ledger row exists regardless
        # of balance arithmetic.
        tx.execute(
            """INSERT INTO token_ledger (person_id, delta, reason, metadata)
               VALUES (%(p)s, 5, 'purchase', '{}'::jsonb)""",
            dict(p=person['uuid']),
        )
        tx.execute(
            """INSERT INTO token_ledger (person_id, delta, reason, metadata)
               VALUES (%(p)s, -3, 'day_pass', %(m)s::jsonb)""",
            dict(p=person['uuid'],
                 m='{"expires_at":"' + expires + '"}'),
        )

    candidates = _make_candidates(11)
    try:
        headers = {'Authorization': f'Bearer {session_token}'}
        for c in candidates:
            res = client.post(
                '/decisions',
                json={'profile_uuid': c['uuid'], 'decision': 'like'},
                headers=headers,
            )
            assert res.status_code == 200, res.get_data(as_text=True)
    finally:
        _cleanup_candidates(candidates)
