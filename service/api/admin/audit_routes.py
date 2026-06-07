"""Audit log tab — filterable view of every destructive admin action."""
from __future__ import annotations

import duotypes as t
from flask import request

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import Q_AUDIT_LIST, Q_AUDIT_COUNT
from database import api_tx


_PAGE_SIZE = 50


@aget('/admin/audit-log')
def get_audit_log(s: t.SessionInfo):
    require_admin(s)
    actor = (request.args.get('actor') or '').strip()
    target = (request.args.get('target') or '').strip()
    action = (request.args.get('action') or '').strip()
    try:
        page = max(1, int(request.args.get('page', '1')))
        days = int(request.args.get('days', '30'))
    except ValueError:
        page, days = 1, 30
    params = {
        'actor': actor, 'target': target, 'action': action, 'days': days,
        'limit': _PAGE_SIZE, 'offset': (page - 1) * _PAGE_SIZE,
    }
    with api_tx('read committed') as tx:
        rows = [dict(r) for r in tx.execute(Q_AUDIT_LIST, params).fetchall()]
        total_row = tx.execute(Q_AUDIT_COUNT, params).fetchone()
    return {
        'rows': rows,
        'total': int(total_row['total']) if total_row else 0,
        'page': page,
        'page_size': _PAGE_SIZE,
    }
