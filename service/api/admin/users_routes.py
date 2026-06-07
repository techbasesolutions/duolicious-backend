"""Users tab routes — list + single-user drilldown.

GET /admin/users        — paginated list with search + filter pills
GET /admin/users/:uuid  — single-user blob (profile + photos + tokens + referrals + audit)
"""
from __future__ import annotations

import duotypes as t
from flask import request

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import (
    Q_USERS_LIST,
    Q_USERS_COUNT,
    Q_USER_PROFILE,
    Q_USER_PHOTOS,
    Q_USER_TOKEN_LEDGER,
    Q_USER_TOKEN_BALANCE,
    Q_USER_REFERRAL_STATS,
    Q_USER_AUDIT_LOG,
    Q_USER_WAITLIST_ANSWERS,
)
from database import api_tx


_PAGE_SIZE = 20

_FILTER_KEYS = (
    'has_person', 'waitlist_only', 'beta_only',
    'unsubscribed', 'has_premium', 'role_admin', 'role_mod',
)


@aget('/admin/users')
def get_admin_users(s: t.SessionInfo):
    require_admin(s)
    q = (request.args.get('q') or '').strip()
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    filters_raw = request.args.get('filters', '')
    active_filters = set(filters_raw.split(',')) if filters_raw else set()
    params = {
        'q': q,
        'q_like': f'%{q}%' if q else '%',
        'limit': _PAGE_SIZE,
        'offset': (page - 1) * _PAGE_SIZE,
        **{k: (k in active_filters) for k in _FILTER_KEYS},
    }
    with api_tx('read committed') as tx:
        rows = [dict(r) for r in tx.execute(Q_USERS_LIST, params).fetchall()]
        total_row = tx.execute(Q_USERS_COUNT, params).fetchone()
    total = int(total_row['total']) if total_row else 0
    return {
        'rows': rows,
        'total': total,
        'page': page,
        'page_size': _PAGE_SIZE,
    }


@aget('/admin/users/<uuid>')
def get_admin_user_detail(s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx('read committed') as tx:
        profile = tx.execute(Q_USER_PROFILE, dict(uuid=uuid)).fetchone()
        if profile is None:
            # Could be a waitlist-only or beta-only row keyed by email.
            # Try to fetch by email from waitlist if uuid is actually an email.
            from flask import abort
            abort(404)
        photos = [dict(r) for r in tx.execute(Q_USER_PHOTOS, dict(uuid=uuid)).fetchall()]
        tokens_ledger = [dict(r) for r in tx.execute(Q_USER_TOKEN_LEDGER, dict(uuid=uuid)).fetchall()]
        balance_row = tx.execute(Q_USER_TOKEN_BALANCE, dict(uuid=uuid)).fetchone()
        referrals = tx.execute(Q_USER_REFERRAL_STATS, dict(uuid=uuid)).fetchone() or {}
        audit = [dict(r) for r in tx.execute(Q_USER_AUDIT_LOG, dict(uuid=uuid)).fetchall()]
        wl_row = tx.execute(Q_USER_WAITLIST_ANSWERS, dict(uuid=uuid)).fetchone()
    return {
        'profile': dict(profile),
        'photos': photos,
        'tokens': {
            'balance': int(balance_row['balance']) if balance_row else 0,
            'ledger': tokens_ledger,
        },
        'referrals': dict(referrals) if referrals else {'pending': 0, 'graduated': 0, 'credited': 0},
        'audit': audit,
        'waitlist_answers': (wl_row or {}).get('answers'),
    }
