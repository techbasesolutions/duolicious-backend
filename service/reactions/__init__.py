"""Chat reactions (v1: heart only).

Pure DB logic. The caller owns the transaction (api_tx) so a toggle is a
single atomic upsert-or-delete. The HTTP layer (service/api/reactions_routes)
adds the Redis real-time publish after the tx commits.

A reaction is "reactor X reacted <kind> to the message with stanza id S, in
the conversation with peer P". PK (message_stanza_id, reactor_id) means one
reaction per person per message - re-toggling removes it.
"""

from __future__ import annotations

from uuid import uuid4


class UnknownPeer(Exception):
    """Raised when peer_uuid does not resolve to a person row."""


_Q_PEER_ID = """
  SELECT id FROM person WHERE uuid = uuid_or_null(%(peer_uuid)s)
"""

_Q_EXISTS = """
  SELECT 1 FROM message_reactions
   WHERE message_stanza_id = %(sid)s AND reactor_id = %(reactor_id)s
   LIMIT 1
"""

_Q_DELETE = """
  DELETE FROM message_reactions
   WHERE message_stanza_id = %(sid)s AND reactor_id = %(reactor_id)s
"""

_Q_INSERT = """
  INSERT INTO message_reactions (message_stanza_id, reactor_id, peer_id, kind)
  VALUES (%(sid)s, %(reactor_id)s, %(peer_id)s, %(kind)s)
  ON CONFLICT (message_stanza_id, reactor_id) DO NOTHING
"""

_Q_LIST = """
  SELECT
      mr.message_stanza_id           AS message_stanza_id,
      reactor.uuid::text             AS reactor_uuid,
      mr.kind                        AS kind
    FROM message_reactions mr
    JOIN person reactor ON reactor.id = mr.reactor_id
   WHERE (mr.reactor_id = %(me_id)s AND mr.peer_id = %(peer_id)s)
      OR (mr.reactor_id = %(peer_id)s AND mr.peer_id = %(me_id)s)
"""


def _resolve_peer_id(tx, peer_uuid: str) -> int:
    row = tx.execute(_Q_PEER_ID, dict(peer_uuid=peer_uuid)).fetchone()
    if not row:
        raise UnknownPeer()
    return row['id']


def toggle(
    tx,
    reactor_uuid: str,
    reactor_id: int,
    peer_uuid: str,
    kind: str = 'heart',
    stanza_id: str | None = None,
) -> dict:
    """Add the reaction if absent, else remove it. Returns
    {action: 'add'|'remove', kind, message_stanza_id, peer_id}.

    Raises UnknownPeer if peer_uuid is unknown.
    """
    sid = stanza_id or str(uuid4())
    peer_id = _resolve_peer_id(tx, peer_uuid)

    exists = tx.execute(
        _Q_EXISTS, dict(sid=sid, reactor_id=reactor_id)
    ).fetchone()

    if exists:
        tx.execute(_Q_DELETE, dict(sid=sid, reactor_id=reactor_id))
        action = 'remove'
    else:
        tx.execute(_Q_INSERT, dict(
            sid=sid, reactor_id=reactor_id, peer_id=peer_id, kind=kind,
        ))
        action = 'add'

    return {
        'action': action,
        'kind': kind,
        'message_stanza_id': sid,
        'peer_id': peer_id,
    }


def list_for_conversation(tx, me_id: int, peer_uuid: str) -> list[dict]:
    """All reactions in the conversation between me and peer (both
    directions). Returns [{message_stanza_id, reactor_uuid, kind}]."""
    peer_id = _resolve_peer_id(tx, peer_uuid)
    cursor = tx.execute(_Q_LIST, dict(me_id=me_id, peer_id=peer_id))
    return list(cursor.fetchall())
