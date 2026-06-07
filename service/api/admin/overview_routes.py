"""Overview tab routes.

GET /admin/whoami — { is_admin, email, person_uuid }. Returns 200
even for non-admins (so the FE can render the Unauthorized screen
without first hitting a 403). Other handlers in this module call
require_admin and return 403 for non-admins."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import is_admin
from database import api_tx


@aget('/admin/whoami')
def get_admin_whoami(s: t.SessionInfo):
    if not s or not s.person_uuid:
        return {'is_admin': False, 'email': None, 'person_uuid': None}
    with api_tx('read committed') as tx:
        admin = is_admin(tx, s.person_uuid)
    return {
        'is_admin': admin,
        'email': s.email if admin else None,
        'person_uuid': s.person_uuid if admin else None,
    }
