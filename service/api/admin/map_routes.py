"""Admin map route.

GET /admin/map — every activated user with a map position, UNFILTERED.
Powers the admin "Show everyone" toggle on /map, which bypasses the normal
discover search (verified-only, gender, age, skip/like, pill filters) so an
operator can see the whole user base on one map — including users who opted
out of the public map. Admin-gated via require_admin.
"""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import Q_ADMIN_MAP_USERS
from database import api_tx


@aget('/admin/map')
def get_admin_map(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        users = [dict(r) for r in tx.execute(Q_ADMIN_MAP_USERS).fetchall()]
    return {'users': users}
