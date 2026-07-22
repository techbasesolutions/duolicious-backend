"""Chat reactions (2026-05-20) - service.reactions toggle + list tests.

Mirrors tests/test_token_rewind.py: real person rows in a fixture,
cleanup via explicit DELETE.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


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
        dict(email=f'reaction-{uuid4()}@example.com'),
    ).fetchone()


@pytest.fixture
def pair():
    from database import api_tx
    with api_tx() as tx:
        me = _make_person(tx)
        peer = _make_person(tx)
    yield {'me': me, 'peer': peer}
    with api_tx() as tx:
        tx.execute(
            "DELETE FROM person WHERE id = ANY(%s)",
            ([me['id'], peer['id']],),
        )


def test_toggle_adds_then_removes(pair):
    from database import api_tx
    from service.reactions import toggle
    me, peer = pair['me'], pair['peer']
    sid = f'stanza-{uuid4()}'
    with api_tx() as tx:
        a = toggle(tx, me['uuid'], me['id'], peer['uuid'], 'heart', stanza_id=sid)
        assert a['action'] == 'add'
        assert a['kind'] == 'heart'
        assert a['message_stanza_id'] == sid
        # Re-toggle on the same stanza_id flips back to remove.
        b = toggle(tx, me['uuid'], me['id'], peer['uuid'], 'heart', stanza_id=sid)
        assert b['action'] == 'remove'


def test_list_returns_both_directions(pair):
    from database import api_tx
    from service.reactions import toggle, list_for_conversation
    me, peer = pair['me'], pair['peer']
    sid_a = f'stanza-{uuid4()}'  # me reacts to a peer message
    sid_b = f'stanza-{uuid4()}'  # peer reacts to my message
    with api_tx() as tx:
        toggle(tx, me['uuid'], me['id'], peer['uuid'], 'heart', stanza_id=sid_a)
        toggle(tx, peer['uuid'], peer['id'], me['uuid'], 'heart', stanza_id=sid_b)
        rows = list_for_conversation(tx, me['id'], peer['uuid'])
    sids = {r['message_stanza_id'] for r in rows}
    assert sid_a in sids and sid_b in sids
    by_sid = {r['message_stanza_id']: r for r in rows}
    assert by_sid[sid_a]['reactor_uuid'] == me['uuid']
    assert by_sid[sid_b]['reactor_uuid'] == peer['uuid']


def test_unknown_peer_raises(pair):
    from database import api_tx
    from service.reactions import toggle, UnknownPeer
    me = pair['me']
    with api_tx() as tx:
        with pytest.raises(UnknownPeer):
            toggle(tx, me['uuid'], me['id'], str(uuid4()), 'heart',
                   stanza_id=f'stanza-{uuid4()}')
