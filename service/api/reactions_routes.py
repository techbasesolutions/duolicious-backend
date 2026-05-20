"""service.api.reactions_routes - HTTP routes for chat reactions.

Imported AT THE BOTTOM of service/api/__init__.py (sibling-module pattern,
same as notifications_routes / moderation_routes) to avoid touching the
brittle top-level multi-import.

Persistence is in service.reactions (owns the api_tx). After the toggle
commits, we publish a <reaction/> frame to the PEER's bare-uuid Redis
channel. The chat server's redis_forward_to_websocket forwards anything on
that channel straight to the peer's socket (identity middleware for the
xmpp subprotocol), so no chat-server change is needed. The publish is
best-effort: a Redis hiccup must never fail the request (the DB write
already succeeded).
"""

from __future__ import annotations

import os

from flask import request
import redis as _redis

import duotypes as t
from service.api.decorators import aget, apost
from database import api_tx
from service.reactions import toggle, list_for_conversation, UnknownPeer

XMPP_DOMAIN = 'ahavah.app'

_REDIS_HOST = os.environ.get('DUO_REDIS_HOST', 'redis')
_REDIS_PORT = int(os.environ.get('DUO_REDIS_PORT', 6379))
# Module-level sync client; decode_responses matches the chat server.
_REDIS = _redis.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)


def _xml_attr(value: str) -> str:
    return (
        value.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def _publish_reaction(
    reactor_uuid: str, peer_uuid: str, stanza_id: str, kind: str, action: str,
) -> None:
    frame = (
        f'<reaction xmlns="ahavah:reactions:0"'
        f' from="{_xml_attr(reactor_uuid)}@{XMPP_DOMAIN}"'
        f' to="{_xml_attr(peer_uuid)}@{XMPP_DOMAIN}"'
        f' message-id="{_xml_attr(stanza_id)}"'
        f' kind="{_xml_attr(kind)}"'
        f' action="{_xml_attr(action)}"/>'
    )
    try:
        _REDIS.publish(peer_uuid, frame)
    except Exception:
        import traceback
        print('reactions: redis publish failed:')
        print(traceback.format_exc())


@apost('/messages/<stanza_id>/reactions')
def post_message_reaction(s: t.SessionInfo, stanza_id: str):
    """Toggle the signed-in user's reaction to message <stanza_id> in the
    conversation with peer_uuid. Body: {peer_uuid, kind?}. v1 accepts only
    kind='heart'. Returns {action, kind}."""
    assert s.person_uuid is not None
    assert s.person_id is not None
    payload = request.get_json(silent=True) or {}
    peer_uuid = payload.get('peer_uuid')
    kind = payload.get('kind', 'heart')
    if not peer_uuid:
        return {'error': 'missing_peer_uuid'}, 400
    if kind != 'heart':
        return {'error': 'unsupported_kind'}, 400
    try:
        with api_tx() as tx:
            result = toggle(
                tx, s.person_uuid, s.person_id, peer_uuid, kind,
                stanza_id=stanza_id,
            )
    except UnknownPeer:
        return {'error': 'unknown_peer'}, 400

    _publish_reaction(
        reactor_uuid=s.person_uuid,
        peer_uuid=peer_uuid,
        stanza_id=stanza_id,
        kind=kind,
        action=result['action'],
    )
    return {'action': result['action'], 'kind': kind}, 200


@aget('/reactions')
def get_reactions(s: t.SessionInfo):
    """All reactions in the conversation with ?with=<peer_uuid> (both
    directions). Returns {reactions: [{message_stanza_id, reactor_uuid,
    kind}]}."""
    assert s.person_id is not None
    peer_uuid = request.args.get('with')
    if not peer_uuid:
        return {'error': 'missing_with'}, 400
    try:
        with api_tx() as tx:
            rows = list_for_conversation(tx, s.person_id, peer_uuid)
    except UnknownPeer:
        return {'error': 'unknown_peer'}, 400
    return {'reactions': rows}, 200
