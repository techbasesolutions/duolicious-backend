"""Admin map route.

GET /admin/map?bbox=s,w,n,e&zoom=Z — viewport-clustered markers for EVERY
activated user, UNFILTERED. Powers the admin "Show everyone" toggle on /map:
it bypasses the normal discover filters (verified-only, gender, age, pill
filters) and the showOnMap opt-out, so an operator can see the whole user
base. Same grid-aggregation as the normal /map/markers, sourced from `person`
instead of a per-viewer cache. Admin-gated via require_admin.
"""
from __future__ import annotations

from flask import request

import duotypes as t

from service.api.decorators import aget
from service.admin import require_admin
from service.admin.queries import Q_ADMIN_MAP_MARKERS
from database import api_tx


@aget('/admin/map')
def get_admin_map(s: t.SessionInfo):
    require_admin(s)

    parts = request.args.get('bbox', '').split(',')
    if len(parts) != 4:
        return 'bbox must be south,west,north,east', 400
    try:
        south, west, north, east = (float(x) for x in parts)
        zoom = int(request.args.get('zoom', '1'))
    except ValueError:
        return 'invalid bbox or zoom', 400

    cell = 90.0 / (2 ** max(1, min(18, zoom)))
    params = dict(south=south, west=west, north=north, east=east, cell=cell)

    with api_tx('read committed') as tx:
        tx.execute('SET LOCAL statement_timeout = 10000')  # 10 seconds
        markers = [dict(r) for r in tx.execute(Q_ADMIN_MAP_MARKERS, params).fetchall()]

    return {'markers': markers}
