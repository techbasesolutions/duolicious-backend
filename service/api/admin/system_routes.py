"""System tab — health snapshot."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import Q_SYSTEM_HEALTH, Q_OTP_24H, Q_VERIFICATION_JOBS
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
        'verification': dict(verification),
    }
