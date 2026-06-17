"""Admin map route.

GET /admin/map — every activated user as an individual point, UNFILTERED.
Powers the admin "Show everyone" toggle on /map: it bypasses the normal
discover filters (verified-only, gender, age, pill filters) and the showOnMap
opt-out, so an operator can see the whole user base. The frontend clusters +
spiderfies client-side. Admin-gated via require_admin.
"""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin, record_audit
from service.admin.queries import Q_ADMIN_MAP_MARKERS
from database import api_tx


@aget('/admin/map')
def get_admin_map(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        tx.execute('SET LOCAL statement_timeout = 10000')  # 10 seconds
        markers = [dict(r) for r in tx.execute(Q_ADMIN_MAP_MARKERS).fetchall()]
        # Audit: this read exposes EVERY user's location + face, including
        # those who opted out of the map. A strong capability -- log each use.
        record_audit(tx, s, 'admin_map_view', metadata={'count': len(markers)})
    return {'markers': markers}
