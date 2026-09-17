"""Public GET/POST /spotlight/card/<token> -- a member reviews and decides
their own Spotlight card (E4, spec 3.1, 3.4, 3.5, section 2).

Mounted unauthenticated, mirroring service/api/spotlight_routes.py and
service/api/unsubscribe_routes.py: the token is HMAC-signed
(service.spotlight.approval) and binds a request_key to the recipient
email, so the link can only decide the one request it was minted for, and
only from the mailbox it was sent to. On top of the signature, a token
carries a single-use nonce (Wave 1 F11) bound to the subject's consent
epoch, so a replayed link reads back 'stale' once the member withdraws
even though the signature and TTL still check out. GET only decodes the
token and reports the current state -- it never mutates, not even the
nonce's `used_at`. POST is the only endpoint that records a decision; the
nonce is consumed only after the decision itself lands (approve_card
returns, or the skip cancellation runs), never before -- a routine 409
(approvals disabled, nothing rendered yet, a photo the member does not
own) leaves the token usable for a retry once the condition clears,
rather than burning a legitimate link on a failed attempt. Choosing a
different photo leaves it usable too: that answers with the new
revision number and no decision has been made yet. So does approving from
a page that showed an older revision than the current one (the approve
body names the revision it showed). A repeat POST
with the same (now-used) token returns the current state idempotently
rather than re-running the decision.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, jsonify, request

from database import api_tx
from service.api.decorators import get, post
from service.api.unsubscribe_routes import unsub_limit
from service.spotlight.approval import CARD_TOKEN_TTL_SECONDS, card_state, parse_card_token
from service.spotlight.nonce import check_nonce, consume_nonce
from service.spotlight.queue import set_status
from service.spotlight.revisions import approve_card, current_revision


def _resolve(token: str) -> tuple[str, str, str]:
    rk, email, nonce, status = parse_card_token(token)
    if status == 'expired':
        abort(410)
    if status != 'ok' or not rk or not email or not nonce:
        abort(400)
    return rk, email, nonce


def _expires_at(token: str) -> str:
    # Token already validated by _resolve; the ts segment is its 3rd part
    # (request_key.email_b64.ts.nonce.sig). Re-splitting here avoids
    # widening parse_card_token's return type just to carry this one
    # derived value.
    ts = int(token.split('.')[2])
    return datetime.fromtimestamp(ts + CARD_TOKEN_TTL_SECONDS, tz=timezone.utc).isoformat()


def _status_of(row: dict) -> str:
    if row['consented']:
        return 'approved'
    if row['status'] == 'cancelled':
        return 'skipped'
    return 'awaiting_member'


def _already(tx, rk: str, row: dict) -> dict:
    """The idempotent shape for 'this token's decision was already
    resolved' -- reached both when check_nonce reports the nonce is
    already used, and when consume_nonce itself reports it lost a race to
    mark the nonce used (defensive: the shared single-connection api_tx
    makes this unreachable today, but a future multi-connection deployment
    would not get that guarantee for free)."""
    fresh = card_state(tx, rk) or row
    return dict(ok=True, already=True, status=_status_of(fresh))


def _card_json(row: dict, token: str, *, stale: bool) -> dict:
    return dict(
        first_name=row['first_name'],
        age=row['age'],
        country=row['country'],
        kind=row['kind'],
        caption=row['caption'],
        photos=row['photos'],
        photo_uuid=row['photo_uuid'],
        revision=row['revision'],
        preview_available=row['preview_available'],
        image_url=row['image_url'],
        status=_status_of(row),
        expires_at=_expires_at(token),
        stale=stale,
    )


@get('/spotlight/card/<token>', limiter=unsub_limit)
def get_spotlight_card(token: str):
    rk, _email, nonce = _resolve(token)
    with api_tx('read committed') as tx:
        row = card_state(tx, rk)
        if not row:
            abort(404)
        stale = False
        if row['subject_person_id'] is not None:
            state = check_nonce(tx, nonce, row['subject_person_id'], 'card')
            stale = state in ('stale', 'invalid')
    return jsonify(_card_json(row, token, stale=stale))


@post('/spotlight/card/<token>', limiter=unsub_limit)
def post_spotlight_card(token: str):
    rk, email, nonce = _resolve(token)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    decision = body.get('decision')
    if decision not in ('approve', 'skip'):
        abort(400)

    # The `with api_tx()` block is inside the try, not the other way round:
    # a ValueError from approve_card (or set_status) must propagate out of
    # the block first, so its __exit__ rolls the transaction back, before
    # this catches it and turns it into a 409. Catching it INSIDE the block
    # would let the block exit normally and commit whatever ran before the
    # raise (Wave 1 F11 fix round 1: that previously left a consumed nonce
    # committed underneath a failed decision).
    try:
        with api_tx() as tx:
            row = card_state(tx, rk)
            if not row:
                abort(404)
            if (row['email'] or '').strip().lower() != email:
                abort(403)

            state = check_nonce(tx, nonce, row['subject_person_id'], 'card')

            if state == 'used':
                # No writes: report the current state idempotently rather
                # than re-running (or erroring on) a decision already made.
                return _already(tx, rk, row)
            if state == 'stale':
                return dict(error='stale'), 410
            if state != 'ok':
                abort(400)  # invalid

            if decision == 'approve':
                photo_uuid = body.get('photo_uuid')
                if not photo_uuid:
                    abort(400)
                # Wave 3d Task 2: the page names the revision it showed, so
                # consent binds to what the member saw. A bool is a Python
                # int subclass, so it is refused explicitly.
                shown_revision = body.get('revision')
                if not isinstance(shown_revision, int) or isinstance(shown_revision, bool):
                    abort(400)
                result = approve_card(tx, rk, row['subject_person_id'], photo_uuid,
                                      shown_revision=shown_revision, nonce=nonce)
                if result == 'new_revision':
                    # Fix wave item 5: choosing a different photo is not a
                    # decision, it is a request for a different card. The
                    # member still has to approve the one that comes back,
                    # so the single-use nonce is NOT consumed here -- burning
                    # it would leave them holding a dead link to a card
                    # nobody can approve. The new revision number tells the
                    # page what it is now waiting on. Wave 3d Task 2: the
                    # same answer, with the same untouched nonce, when the
                    # page approved an older revision than the current one.
                    fresh = current_revision(tx, rk)
                    return dict(ok=True, result=result,
                                revision=fresh['revision'] if fresh else None)
                if not consume_nonce(tx, nonce):
                    return _already(tx, rk, row)
                return dict(ok=True, result=result)

            # skip: cancel every row of this request that is still awaiting
            # the member's decision. Nothing left in that state means a
            # previous call (approve or skip) already resolved it -- report
            # that as the same idempotent "already" shape rather than
            # erroring, and still burn the nonce (the outcome is settled
            # either way).
            ids = [r['id'] for r in tx.execute(
                "SELECT id FROM publishing_queue WHERE request_key = %(rk)s AND status = 'awaiting_member'",
                dict(rk=rk)).fetchall()]
            if not ids:
                consume_nonce(tx, nonce)
                return dict(ok=True, already=True)
            for qid in ids:
                set_status(tx, qid, 'cancelled', error='member_skipped')
            if not consume_nonce(tx, nonce):
                return _already(tx, rk, row)
            return dict(ok=True)
    except ValueError as e:
        return dict(error=str(e)), 409
