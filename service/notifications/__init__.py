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
from typing import Any, Dict, Literal, Optional


EventKind = Literal[
    "match", "message", "like", "weekly", "verification", "profile_view"
]

# Map each event kind to the column on notification_preference that
# gates it. Keeping the mapping in one place means the cron + handlers
# don't have to know column names.
_EVENT_COLUMN: Dict[EventKind, str] = {
    "match":        "push_matches",
    "message":      "push_messages",
    "like":         "push_likes",
    "weekly":       "push_weekly_digest",
    "verification": "push_verification",
    "profile_view": "push_profile_views",
}

# Defaults that apply when notification_preference has no row for the
# user. Lazy-insert pattern: the row is only created on first PATCH.
_EVENT_DEFAULTS: Dict[EventKind, bool] = {
    "match":        True,
    "message":      True,
    "like":         False,
    "weekly":       False,
    "verification": True,
    "profile_view": False,
}


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


# Prune a subscription after this many consecutive failed sends (transient
# errors that never recover), in addition to the immediate 404/410 prune.
_MAX_PUSH_FAILURES = 8


def _send_to_user_blocking(person_id: int, payload: Dict[str, Any]):
    """Look up every push_subscription for the user and send to each.
    Prune rows that 404/410 immediately, or that cross _MAX_PUSH_FAILURES
    consecutive failures. A successful send resets the failure counter.
    Push is best-effort - other errors are logged but swallowed."""
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

    dead_ids = []    # 404/410 - revoked/expired, prune now
    failed_ids = []  # other error - bump the consecutive-failure counter
    ok_ids = []      # success - reset the counter
    for r in rows:
        try:
            status = _send_one(r['endpoint'], r['p256dh'], r['auth'], payload)
            if status in (404, 410):
                dead_ids.append(r['id'])
            elif status is None:
                ok_ids.append(r['id'])
            else:
                failed_ids.append(r['id'])
        except Exception:
            failed_ids.append(r['id'])
            print(traceback.format_exc())

    with api_tx() as tx:
        if ok_ids:
            tx.execute(
                "UPDATE push_subscription SET consecutive_failures = 0 "
                "WHERE id = ANY(%(ids)s)",
                dict(ids=ok_ids),
            )
        if failed_ids:
            tx.execute(
                "UPDATE push_subscription "
                "SET consecutive_failures = consecutive_failures + 1 "
                "WHERE id = ANY(%(ids)s)",
                dict(ids=failed_ids),
            )
        # Prune the freshly-dead (404/410) plus anything that has now
        # crossed the consecutive-failure threshold.
        tx.execute(
            "DELETE FROM push_subscription "
            "WHERE id = ANY(%(dead)s) "
            "   OR consecutive_failures >= %(thresh)s",
            dict(dead=dead_ids, thresh=_MAX_PUSH_FAILURES),
        )


def _allowed_for_event(person_id: int, event_kind: EventKind) -> bool:
    """Query notification_preference and return whether this kind of
    event is allowed for the user. Missing row → documented default."""
    column = _EVENT_COLUMN[event_kind]
    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            f"""
            SELECT {column} AS allowed
              FROM notification_preference
             WHERE person_id = %(person_id)s
            """,
            dict(person_id=person_id),
        ).fetchone()
    if row is None:
        return _EVENT_DEFAULTS[event_kind]
    return bool(row['allowed'])


def send_to_user_safe(
    person_id: int,
    title: str,
    body: str,
    url: str = '/',
    tag: Optional[str] = None,
    *,
    event_kind: Optional[EventKind] = None,
):
    """Fire-and-forget push. Always returns immediately; the actual
    network I/O happens on a background thread so callers (request
    handlers, decision endpoints) don't pay the latency.

    Phase W cutover (mig 0013): when `event_kind` is provided the
    function short-circuits if the user's notification_preference row
    has the corresponding column = FALSE. Missing row → documented
    default per `_EVENT_DEFAULTS`. event_kind=None preserves the
    legacy unconditional send (only the helper test harness should
    rely on this)."""
    if not PUSH_ENABLED:
        return
    if event_kind is not None and not _allowed_for_event(person_id, event_kind):
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


def get_notification_preferences(s):
    """Return the user's per-event push preferences. If the row
    doesn't exist yet (legacy user, never PATCHed), return the
    documented defaults — matches + messages ON, likes + weekly OFF —
    so the UI can render the toggles without an extra mount-time write."""
    if s.person_id is None:
        return 'Not signed in', 401

    # Phase 2 (mig 0029): full per-event x per-channel matrix. Defaults
    # mirror the migration / approved matrix so a legacy row-less user gets
    # the right toggle state without a mount-time write.
    _MATRIX_DEFAULTS = dict(
        push_matches=True,        email_matches=True,
        push_messages=True,       email_messages=True,
        push_likes=False,         email_likes=False,
        push_verification=True,   email_verification=True,
        push_profile_views=False, email_profile_views=False,
        push_weekly_digest=False,
    )
    cols = list(_MATRIX_DEFAULTS.keys())

    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            f"SELECT {', '.join(cols)} FROM notification_preference "
            "WHERE person_id = %(person_id)s",
            dict(person_id=s.person_id),
        ).fetchone()
    if row is None:
        return dict(_MATRIX_DEFAULTS)
    return {c: row[c] for c in cols}


def patch_notification_preferences(req, s):
    """Upsert any subset of the four toggles. Uses INSERT … ON CONFLICT
    so the row is created on first write with whatever defaults the
    client didn't override. Subsequent writes only touch the columns
    the client explicitly included."""
    if s.person_id is None:
        return 'Not signed in', 401

    # Build the SET clause from non-None fields only. Pydantic gives us
    # `__pydantic_fields_set__` listing what the client actually sent.
    updates = {
        k: getattr(req, k)
        for k in req.__pydantic_fields_set__
        if getattr(req, k) is not None
    }
    if not updates:
        return '', 204

    cols = list(updates.keys())
    params = dict(person_id=s.person_id, **updates)
    set_clause = ', '.join(f'{c} = %({c})s' for c in cols)
    insert_cols = ', '.join(['person_id'] + cols)
    insert_vals = ', '.join(['%(person_id)s'] + [f'%({c})s' for c in cols])
    q = f"""
    INSERT INTO notification_preference ({insert_cols}, updated_at)
    VALUES ({insert_vals}, NOW())
    ON CONFLICT (person_id) DO UPDATE
       SET {set_clause}, updated_at = NOW()
    """
    from database import api_tx
    with api_tx() as tx:
        tx.execute(q, params)
    return '', 204
