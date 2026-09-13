from __future__ import annotations

import duotypes as t
from database import api_tx
from service.admin import require_admin
from service.api.decorators import aget
from service.growth.queries import growth_stats

@aget('/admin/growth/stats')
def get_admin_growth_stats(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        return growth_stats(tx)
