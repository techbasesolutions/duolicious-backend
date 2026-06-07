"""Admin dashboard — service-layer module.

Every /admin/* endpoint:
  1. Calls require_admin(s) FIRST. Returns 403 for non-admins.
  2. For mutations: calls record_audit(tx, s, action, target, metadata)
     in the SAME api_tx as the mutation so they commit atomically.

Public surface:
  require_admin(s)             — raises HTTPException(403) if not admin
  record_audit(tx, s, ...)     — writes one admin_audit_log row
  is_admin(tx, person_uuid)    — read-only check (used by /admin/whoami)

Query helpers per tab land in service/admin/queries/* and are
imported here as each phase ships."""
from __future__ import annotations

import json
from typing import Any, Optional

from database import api_tx
from flask import abort
import duotypes as t

from service.admin.queries import Q_IS_ADMIN, Q_INSERT_AUDIT


def is_admin(tx, person_uuid: str) -> bool:
    """1-row gate check. tx is a psycopg cursor owned by the caller."""
    if not person_uuid:
        return False
    row = tx.execute(Q_IS_ADMIN, dict(uuid=person_uuid)).fetchone()
    return bool(row and row.get('is_admin'))


def require_admin(s: t.SessionInfo) -> None:
    """First line of every /admin/* handler. Aborts with 403 if the
    caller's session person_uuid is not in the admin role. Opens its
    own read-only tx — the handler can still open its own write tx."""
    if not s or not s.person_uuid:
        abort(403)
    with api_tx('read committed') as tx:
        if not is_admin(tx, s.person_uuid):
            abort(403)


def record_audit(
    tx,
    s: t.SessionInfo,
    action: str,
    target_email: Optional[str] = None,
    target_uuid: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Write one admin_audit_log row. Caller MUST be inside an api_tx
    that also holds the mutation being audited — so the audit + the
    mutation commit (or roll back) together."""
    row = tx.execute(
        Q_INSERT_AUDIT,
        dict(
            actor_email=s.email,
            actor_uuid=s.person_uuid,
            action=action,
            target_email=target_email,
            target_uuid=target_uuid,
            metadata=json.dumps(metadata or {}),
        ),
    ).fetchone()
    return str(row['id'])
