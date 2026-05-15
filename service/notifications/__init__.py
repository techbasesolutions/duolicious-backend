"""
service.notifications - Phase W push notifications (VAPID web push).

The frontend calls swReg.pushManager.subscribe({ applicationServerKey:
NEXT_PUBLIC_VAPID_PUBLIC_KEY, ... }) on user opt-in and POSTs the
resulting PushSubscription JSON to /notifications/subscribe. We persist
one row per (person_id, endpoint) in `push_subscription` (migration
0010) and use pywebpush + the VAPID private key to push notifications
on relevant server-side events (currently: match created).

Cleanup: when pywebpush returns 404 or 410 we delete the row - that
endpoint has been revoked or expired. Other errors we log and ignore.

Trigger surfaces:
  - decisions.post_decisions - fire on mutual-like (match created)
  - chat.* - message-deliver hook deferred (covered by inbox unread badge)

IMPORTANT: do not import this module from `service/api/__init__.py`'s
top-level multi-import block - that block is fragile and adding new
sibling modules has caused obscure namespace-package import errors in
the past. Use lazy `from service.notifications import send_to_user_safe`
inside the call site instead. The HTTP routes for this module are
registered via `service.api.notifications_routes` which is imported at
the bottom of `service/api/__init__.py` after the main decorator
infrastructure is ready.
"""

import json
import os
import threading
import traceback
from typing import Any, Dict, Optional


VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT = os.environ.get('VAPID_SUBJECT', 'mailto:admin@ahavah.app')

PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

if not PUSH_ENABLED:
    print('service.notifications: VAPID keys missing; push notifications disabled')


def post_subscribe(s, req):
    """Persist a PushSubscription. `s` is t.SessionInfo, `req` is
    t.PostNotificationsSubscribe. Imported via str-typing so this
    module doesn't pull in `duotypes` at import time (keeps the import
    graph minimal and safe to load anywhere)."""
    if s.person_id is None:
        return 'Not signed in', 401

    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO push_subscription (person_id, endpoint, p256dh, auth)
            VALUES (%(person_id)s, %(endpoint)s, %(p256dh)s, %(auth)s)
            ON CONFLICT (person_id, endpoint) DO UPDATE
              SET p256dh    = EXCLUDED.p256dh,
                  auth      = EXCLUDED.auth,
                  updated_at = NOW()
            """,
            dict(
                person_id=s.person_id,
                endpoint=req.endpoint,
                p256dh=req.keys.p256dh,
                auth=req.keys.auth,
            ),
        )

    return {'ok': True}


def delete_subscribe(s, req):
    if s.person_id is None:
        return 'Not signed in', 401

    from database import api_tx
    with api_tx() as tx:
        tx.execute(
            """
            DELETE FROM push_subscription
             WHERE person_id = %(person_id)s
               AND endpoint  = %(endpoint)s
            """,
            dict(person_id=s.person_id, endpoint=req.endpoint),
        )

    return {'ok': True}


def _send_one(endpoint: str, p256dh: str, auth: str, payload: Dict[str, Any]) -> Optional[int]:
    """Send a single push. Returns the HTTP status code on a typed error
    (so the caller can prune dead subscriptions on 404/410), None on
    success, and re-raises only catastrophic failures."""
    from pywebpush import webpush, WebPushException

    try:
        webpush(
            subscription_info={
                'endpoint': endpoint,
                'keys': {'p256dh': p256dh, 'auth': auth},
            },
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={'sub': VAPID_SUBJECT},
            ttl=60 * 60 * 24,
        )
        return None
    except WebPushException as exc:
        status = getattr(exc.response, 'status_code', None)
        return status


def _send_to_user_blocking(person_id: int, payload: Dict[str, Any]):
    """Look up every push_subscription for the user and send to each.
    Prune rows whose endpoint returns 404 / 410. Any other error is
    logged but swallowed - push is best-effort."""
    if not PUSH_ENABLED:
        return

    from database import api_tx
    with api_tx() as tx:
        rows = tx.execute(
            """
            SELECT id, endpoint, p256dh, auth
              FROM push_subscription
             WHERE person_id = %(person_id)s
            """,
            dict(person_id=person_id),
        ).fetchall()

    if not rows:
        return

    dead_ids = []
    for r in rows:
        try:
            status = _send_one(r['endpoint'], r['p256dh'], r['auth'], payload)
            if status in (404, 410):
                dead_ids.append(r['id'])
        except Exception:
            print(traceback.format_exc())

    if dead_ids:
        with api_tx() as tx:
            tx.execute(
                "DELETE FROM push_subscription WHERE id = ANY(%(ids)s)",
                dict(ids=dead_ids),
            )


def send_to_user_safe(
    person_id: int,
    title: str,
    body: str,
    url: str = '/',
    tag: Optional[str] = None,
):
    """Fire-and-forget push. Always returns immediately; the actual
    network I/O happens on a background thread so callers (request
    handlers, decision endpoints) don't pay the latency."""
    if not PUSH_ENABLED:
        return

    payload: Dict[str, Any] = {
        'title': title,
        'body': body,
        'url': url,
    }
    if tag is not None:
        payload['tag'] = tag

    threading.Thread(
        target=_send_to_user_blocking,
        kwargs=dict(person_id=person_id, payload=payload),
        daemon=True,
    ).start()
