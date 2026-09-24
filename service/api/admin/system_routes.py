"""System tab — health snapshot."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import (
    Q_SYSTEM_HEALTH, Q_OTP_24H, Q_OUTBOX_HEALTH, Q_VERIFICATION_JOBS)
from service.verificationlease import (
    VERIFICATION_LEASE_SECONDS,
    VERIFICATION_MAX_REAPS,
)
from database import api_tx


@aget('/admin/system/health')
def get_system_health(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        health = tx.execute(Q_SYSTEM_HEALTH).fetchone() or {}
        otp = tx.execute(Q_OTP_24H).fetchone() or {}
        # Campaign email delivery. In the same transaction as the rest: the
        # api connection lock is not reentrant, so a second api_tx here
        # would deadlock the request.
        outbox = tx.execute(Q_OUTBOX_HEALTH).fetchone() or {}
        # The counts are read against the same lease and ceiling the cron
        # claims with, so what the operator calls stuck is exactly what the
        # reaper gave up on.
        verification = tx.execute(
            Q_VERIFICATION_JOBS,
            dict(
                lease_seconds=VERIFICATION_LEASE_SECONDS,
                max_reaps=VERIFICATION_MAX_REAPS,
            ),
        ).fetchone() or {}
    return {
        'health': dict(health),
        'otp': dict(otp),
        # isoformat() rather than letting Flask's JSON provider have the raw
        # datetime: its default is an RFC 2822 HTTP-date ("Thu, 24 Sep 2026
        # 10:56:56 GMT"), every other timestamp on the admin surface is ISO
        # 8601, and the TypeScript that reads this one is typed and tested
        # against ISO.
        'outbox': dict(outbox, oldest_queued_at=(
            outbox['oldest_queued_at'].isoformat()
            if outbox.get('oldest_queued_at') else None)),
        'verification': dict(verification),
    }
