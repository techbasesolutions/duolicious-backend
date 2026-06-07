"""System tab — health snapshot."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import Q_SYSTEM_HEALTH, Q_OTP_24H
from database import api_tx


@aget('/admin/system/health')
def get_system_health(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        health = tx.execute(Q_SYSTEM_HEALTH).fetchone() or {}
        otp = tx.execute(Q_OTP_24H).fetchone() or {}
    return {
        'health': dict(health),
        'otp': dict(otp),
    }
