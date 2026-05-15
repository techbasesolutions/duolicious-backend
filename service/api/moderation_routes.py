"""
service.api.moderation_routes — admin/mod-gated routes.

Imported at the bottom of `service/api/__init__.py` (alongside
notifications_routes per the same pattern). Keeps the brittle
top-level multi-import block in __init__.py untouched — adding new
sibling modules to that block has historically caused obscure
namespace-package import failures.

Lazy-imports `service.moderation` inside each handler so this module
loads cleanly even if moderation has runtime deps.
"""

import duotypes as t

from service.api.decorators import aget


@aget('/admin/reports')
def get_admin_reports(s: t.SessionInfo):
    """List recent abuse reports for admin/mod review. Gated on
    person.roles && ARRAY['admin','mod']; returns 403 otherwise."""
    from service import moderation
    return moderation.get_admin_reports(s)
