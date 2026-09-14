"""Public GET/POST /spotlight/card/<token> -- a member reviews and decides
their own Spotlight card (E4, spec 3.1, 3.4, 3.5, section 2).

Mounted unauthenticated, mirroring service/api/spotlight_routes.py and
service/api/unsubscribe_routes.py: the token is HMAC-signed
(service.spotlight.approval) and binds a request_key to the recipient
email, so the link can only decide the one request it was minted for, and
only from the mailbox it was sent to. GET only decodes the token and
reports the current state -- it never mutates. POST is the only endpoint
that records a decision, and both decisions (approve, skip) are
idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, jsonify, request

from database import api_tx
from service.api.decorators import get, post
from service.api.unsubscribe_routes import unsub_limit
from service.spotlight.approval import CARD_TOKEN_TTL_SECONDS, card_state, parse_card_token
from service.spotlight.queue import set_status
from service.spotlight.revisions import approve_card


def _resolve(token: str) -> tuple[str, str]:
    rk, email, status = parse_card_token(token)
    if status == 'expired':
        abort(410)
    if status != 'ok' or not rk or not email:
        abort(400)
    return rk, email


def _expires_at(token: str) -> str:
    # Token already validated by _resolve; the ts segment is its 3rd part
    # (request_key.email_b64.ts.sig). Re-splitting here avoids widening
    # parse_card_token's return type just to carry this one derived value.
    ts = int(token.split('.')[2])
    return datetime.fromtimestamp(ts + CARD_TOKEN_TTL_SECONDS, tz=timezone.utc).isoformat()


def _status_of(row: dict) -> str:
    if row['consented']:
        return 'approved'
    if row['status'] == 'cancelled':
        return 'skipped'
    return 'awaiting_member'


def _card_json(row: dict, token: str) -> dict:
    return dict(
        first_name=row['first_name'],
        age=row['age'],
        country=row['country'],
        kind=row['kind'],
        caption=row['caption'],
        photos=row['photos'],
        revision=row['revision'],
        preview_available=row['preview_available'],
        image_url=row['image_url'],
        status=_status_of(row),
        expires_at=_expires_at(token),
    )


@get('/spotlight/card/<token>', limiter=unsub_limit)
def get_spotlight_card(token: str):
    rk, _email = _resolve(token)
    with api_tx('read committed') as tx:
        row = card_state(tx, rk)
    if not row:
        abort(404)
    return jsonify(_card_json(row, token))


@post('/spotlight/card/<token>', limiter=unsub_limit)
def post_spotlight_card(token: str):
    rk, email = _resolve(token)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    decision = body.get('decision')
    if decision not in ('approve', 'skip'):
        abort(400)

    with api_tx() as tx:
        row = card_state(tx, rk)
        if not row:
            abort(404)
        if (row['email'] or '').strip().lower() != email:
            abort(403)

        if decision == 'approve':
            photo_uuid = body.get('photo_uuid')
            if not photo_uuid:
                abort(400)
            try:
                result = approve_card(tx, rk, row['subject_person_id'], photo_uuid)
            except ValueError as e:
                abort(409, str(e))
            return dict(ok=True, result=result)

        # skip: cancel every row of this request that is still awaiting the
        # member's decision. Nothing left in that state means a previous
        # call (approve or skip) already resolved it -- report that as the
        # same idempotent "already" shape rather than erroring.
        ids = [r['id'] for r in tx.execute(
            "SELECT id FROM publishing_queue WHERE request_key = %(rk)s AND status = 'awaiting_member'",
            dict(rk=rk)).fetchall()]
        if not ids:
            return dict(ok=True, already=True)
        for qid in ids:
            set_status(tx, qid, 'cancelled', error='member_skipped')
        return dict(ok=True)
