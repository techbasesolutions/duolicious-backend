"""Economy tab routes — token ledger, entitlement holders, subscriptions."""
from __future__ import annotations

import duotypes as t
from flask import request

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import (
    Q_LEDGER, Q_LEDGER_COUNT, Q_LEDGER_KPIS,
    Q_ENTITLEMENT_HOLDERS, Q_ENTITLEMENT_KPIS,
    Q_SUBSCRIPTIONS_KPIS, Q_SUBSCRIPTIONS_ROWS,
)
from database import api_tx


_PAGE_SIZE = 20


@aget('/admin/economy/ledger')
def get_economy_ledger(s: t.SessionInfo):
    require_admin(s)
    reason = (request.args.get('reason') or '').strip()
    email = (request.args.get('email') or '').strip()
    try:
        page = max(1, int(request.args.get('page', '1')))
        days = int(request.args.get('days', '30'))
    except ValueError:
        page, days = 1, 30
    params = {
        'reason': reason,
        'email': email,
        'email_like': f'%{email}%' if email else '',
        'days': days,
        'limit': _PAGE_SIZE,
        'offset': (page - 1) * _PAGE_SIZE,
    }
    with api_tx('read committed') as tx:
        rows = [dict(r) for r in tx.execute(Q_LEDGER, params).fetchall()]
        total_row = tx.execute(Q_LEDGER_COUNT, params).fetchone()
        kpis = tx.execute(Q_LEDGER_KPIS).fetchone() or {}
    return {
        'rows': rows,
        'total': int(total_row['total']) if total_row else 0,
        'page': page,
        'page_size': _PAGE_SIZE,
        'kpis': dict(kpis),
    }


@aget('/admin/economy/entitlements')
def get_economy_entitlements(s: t.SessionInfo):
    require_admin(s)
    name = (request.args.get('name') or 'premium').strip()
    try:
        expires_within_days = int(request.args.get('expires_within_days', '0'))
    except ValueError:
        expires_within_days = 0
    params = {'name': name, 'expires_within_days': expires_within_days}
    with api_tx('read committed') as tx:
        holders = [dict(r) for r in tx.execute(Q_ENTITLEMENT_HOLDERS, params).fetchall()]
        kpis = tx.execute(Q_ENTITLEMENT_KPIS, params).fetchone() or {}
    return {'holders': holders, 'kpis': dict(kpis)}


@aget('/admin/economy/subscriptions')
def get_economy_subscriptions(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        kpis = tx.execute(Q_SUBSCRIPTIONS_KPIS).fetchone() or {}
        rows = [dict(r) for r in tx.execute(Q_SUBSCRIPTIONS_ROWS).fetchall()]
    return {'kpis': dict(kpis), 'rows': rows}
