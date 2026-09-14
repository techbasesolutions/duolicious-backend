"""Shared-secret auth for the spotlight scheduler.

The queue endpoints under /admin/growth are driven by two callers: a human
admin holding a bearer session, and an unattended cron worker that has no
session at all. The worker presents `X-Growth-Cron: <secret>` instead.

Fails closed: with `AHAVAH_GROWTH_CRON_SECRET` unset (the default), no header
value can ever authenticate, so a misconfigured environment loses cron access
rather than opening the endpoints to anyone.
"""
from __future__ import annotations

import hmac

from flask import request

from service.admin import require_admin
from service.config import GROWTH_CRON_SECRET


def is_cron_request() -> bool:
    """True when the request carries the correct cron secret header."""
    if not GROWTH_CRON_SECRET:
        return False
    given = request.headers.get('X-Growth-Cron', '')
    return bool(given) and hmac.compare_digest(given, GROWTH_CRON_SECRET)


def require_admin_or_cron(s) -> None:
    """Gate for endpoints both the admin UI and the cron worker call.

    `s` may be None (a cron call has no session). Aborts 403 when neither
    the cron secret nor an admin session is present.
    """
    if is_cron_request():
        return
    require_admin(s)
