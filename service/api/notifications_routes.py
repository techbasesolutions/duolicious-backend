"""
service.api.notifications_routes - HTTP routes for /notifications/*.

Imported AT THE BOTTOM of `service/api/__init__.py` after the Flask
app + decorator infrastructure (`apost`, `adelete`, `validate`) is
ready. This avoids touching the brittle top-level multi-import in
`service/api/__init__.py` whose contents have historically caused
namespace-package import failures whenever a new sibling module is
added to the list.

The handlers themselves use lazy imports of `service.notifications`
inside the function bodies so that loading this module doesn't pull
in pywebpush / database transitively at import time. Push routes
should never block the primary import chain even if a runtime
dependency is missing.
"""

import duotypes as t

from service.api.decorators import (
    aget,
    apatch,
    apost,
    adelete,
    validate,
)


@apost('/notifications/subscribe')
@validate(t.PostNotificationsSubscribe)
def post_notifications_subscribe(req: t.PostNotificationsSubscribe, s: t.SessionInfo):
    """Persist a browser PushSubscription for the signed-in user.
    Idempotent on (person_id, endpoint) - re-subscribing on the same
    device updates the keys + bumps updated_at."""
    from service import notifications
    return notifications.post_subscribe(s, req)


@adelete('/notifications/subscribe')
@validate(t.DeleteNotificationsSubscribe)
def delete_notifications_subscribe(req: t.DeleteNotificationsSubscribe, s: t.SessionInfo):
    """Remove a PushSubscription. Frontend calls this on explicit
    user opt-out + on swReg.pushManager.subscription.unsubscribe()."""
    from service import notifications
    return notifications.delete_subscribe(s, req)


@aget('/notifications/preferences')
def get_notifications_preferences(s: t.SessionInfo):
    """Return the signed-in user's per-event push preferences (mig 0013).
    Lazy default if the row doesn't exist yet."""
    from service import notifications
    return notifications.get_notification_preferences(s)


@apatch('/notifications/preferences')
@validate(t.PatchNotificationPreferences)
def patch_notifications_preferences(
    req: t.PatchNotificationPreferences,
    s: t.SessionInfo,
):
    """Upsert any subset of the four event toggles. Body fields are all
    optional so the client can flip one without resetting the others."""
    from service import notifications
    return notifications.patch_notification_preferences(req, s)


@apost('/notifications/test')
def post_notifications_test(s: t.SessionInfo):
    """Fire a test push to the signed-in user's devices so they can confirm
    push works on THIS device. Bypasses per-event prefs (it's a self-test)."""
    if s.person_id is None:
        return 'Not signed in', 401
    from service import notifications
    notifications.send_to_user_safe(
        s.person_id,
        title="Ahavah",
        body="Push notifications are working.",
        url="/",
    )
    return '', 204
