"""Public POST /feedback - general user feedback, emailed to the admin.

Sibling module (imported at the bottom of service/api/__init__.py) to avoid
the brittle top-level import block, mirroring waitlist_routes.py.
Unauthenticated and anonymous-friendly (email is optional); rate-limited by IP
via a dedicated shared limiter. No database: the body is validated by the
PostFeedback model and emailed (fire-and-forget) to admin@techbaseltd.com.
"""

from __future__ import annotations

import duotypes as t
from service.api.decorators import post, validate, limiter, _is_private_ip
from emails.feedback import send_feedback_async

# Dedicated shared IP limiter (separate scope from OTP/waitlist). Private IPs
# (localhost, the test harness) are exempt, same as the other public routes.
feedback_limit = limiter.shared_limit(
    "5 per minute",
    scope="feedback",
    exempt_when=_is_private_ip,
)


@post('/feedback', limiter=feedback_limit)
@validate(t.PostFeedback)
def post_feedback(req: t.PostFeedback):
    # Fire-and-forget so the response isn't blocked on SMTP; failures are
    # swallowed + logged inside the helper (feedback is non-critical).
    send_feedback_async(
        category=req.category,
        message=req.message,
        email=req.email,
        path=req.path,
        user_agent=req.user_agent,
    )
    return {'ok': True}
