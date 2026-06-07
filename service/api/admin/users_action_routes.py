"""User-management action endpoints — every mutation is audit-logged.

Endpoints:
  POST   /admin/users/:uuid/entitlements         — grant
  DELETE /admin/users/:uuid/entitlements/:name   — revoke
  POST   /admin/users/:uuid/tokens               — manual credit/debit
  PATCH  /admin/users/:uuid/roles                — grant/revoke admin/mod
  POST   /admin/users/:uuid/deactivate           — soft delete
  POST   /admin/users/:uuid/reactivate           — undo soft delete
  DELETE /admin/users/:uuid                      — hard delete (requires confirm_email)
  POST   /admin/users/:uuid/clear-onboardee      — wipe onboardee + sessions
  POST   /admin/users/:uuid/resend-otp           — send a fresh OTP

Every mutation calls require_admin(s) FIRST, then performs the mutation
and writes one admin_audit_log row in the same api_tx EXCEPT for the
two entitlement endpoints — service.entitlements.grant/revoke open
their own tx, so the audit is written in an adjacent tx. Acceptable
trade-off because entitlements grant/revoke is idempotent.
"""
from __future__ import annotations

import secrets

import duotypes as t
from flask import abort, request

from service.api.decorators import apost, apatch, adelete, validate
from service.admin import require_admin, record_audit
from service import entitlements
from service.tokens import credit as tokens_credit, debit as tokens_debit
from database import api_tx
from duohash import sha512
from antiabuse.antispam.signupemail import normalize_email
from service.person.sql import Q_INSERT_DUO_SESSION


# ----- helpers -------------------------------------------------------------

_Q_PERSON_BY_UUID = """
    SELECT id, email, COALESCE(roles, ARRAY[]::text[]) AS roles
      FROM person WHERE uuid = %(uuid)s
"""

_Q_SOFT_DELETE = """
    UPDATE person SET activated = FALSE WHERE uuid = %(uuid)s
    RETURNING email
"""

_Q_REACTIVATE = """
    UPDATE person SET activated = TRUE WHERE uuid = %(uuid)s
    RETURNING email
"""

_Q_HARD_DELETE = """
    DELETE FROM person WHERE uuid = %(uuid)s
    RETURNING email
"""

_Q_PATCH_ROLES = """
    UPDATE person
       SET roles = %(roles)s
     WHERE uuid = %(uuid)s
    RETURNING email, roles
"""

# Wipe onboardee + any duo_session rows tied to the person's email. The
# WITH clause runs first; the final DELETE returns the email for audit.
_Q_CLEAR_ONBOARDEE = """
    WITH target AS (
        SELECT email FROM person WHERE uuid = %(uuid)s
    ),
    del_session AS (
        DELETE FROM duo_session
         WHERE email IN (SELECT email FROM target)
        RETURNING email
    )
    DELETE FROM onboardee
     WHERE email IN (SELECT email FROM target)
    RETURNING email
"""


# ----- ENTITLEMENTS --------------------------------------------------------

@apost('/admin/users/<uuid>/entitlements')
@validate(t.PostGrantEntitlement)
def post_grant_entitlement(req: t.PostGrantEntitlement, s: t.SessionInfo, uuid: str):
    require_admin(s)
    # service.entitlements.grant takes int person.id, not uuid — look up first.
    with api_tx('read committed') as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
    if row is None:
        abort(404)
    person_id = int(row['id'])
    target_email = row['email']
    # grant() opens its OWN api_tx; we cannot piggyback the audit on it.
    # If the process dies between these two statements the entitlement
    # change is silently un-audited. Acceptable because grant is idempotent.
    changed = entitlements.grant(person_id, req.name, expires_at=req.expires_at)
    with api_tx() as tx:
        record_audit(
            tx, s, 'grant_entitlement',
            target_email=target_email, target_uuid=uuid,
            metadata={
                'name': req.name,
                'expires_at': req.expires_at.isoformat() if req.expires_at else None,
                'reason': req.reason,
                'changed': changed,
            },
        )
    return {'ok': True, 'changed': changed}


@adelete('/admin/users/<uuid>/entitlements/<name>')
def delete_revoke_entitlement(s: t.SessionInfo, uuid: str, name: str):
    require_admin(s)
    with api_tx('read committed') as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
    if row is None:
        abort(404)
    person_id = int(row['id'])
    target_email = row['email']
    # revoke() opens its OWN api_tx — same caveat as grant() above.
    changed = entitlements.revoke(person_id, name)
    with api_tx() as tx:
        record_audit(
            tx, s, 'revoke_entitlement',
            target_email=target_email, target_uuid=uuid,
            metadata={'name': name, 'changed': changed},
        )
    return {'ok': True, 'changed': changed}


# ----- TOKENS --------------------------------------------------------------

@apost('/admin/users/<uuid>/tokens')
@validate(t.PostTokenAdjust)
def post_token_adjust(req: t.PostTokenAdjust, s: t.SessionInfo, uuid: str):
    require_admin(s)
    if req.delta == 0:
        abort(400)
    with api_tx() as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        target_email = row['email']
        amount = abs(req.delta)
        if req.delta > 0:
            tokens_credit(
                tx, uuid, amount,
                reason='admin_credit',
                metadata={'admin': s.email, 'reason': req.reason},
            )
        else:
            tokens_debit(
                tx, uuid, amount,
                reason='admin_debit',
                metadata={'admin': s.email, 'reason': req.reason},
            )
        record_audit(
            tx, s, 'token_adjust',
            target_email=target_email, target_uuid=uuid,
            metadata={'delta': req.delta, 'reason': req.reason},
        )
    return {'ok': True, 'delta': req.delta}


# ----- ROLES ---------------------------------------------------------------

@apatch('/admin/users/<uuid>/roles')
@validate(t.PatchRoles)
def patch_roles(req: t.PatchRoles, s: t.SessionInfo, uuid: str):
    require_admin(s)
    allowed = {'admin', 'mod'}
    add = [r for r in req.add if r in allowed]
    remove = [r for r in req.remove if r in allowed]
    with api_tx() as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        target_email = row['email']
        current = set(row['roles'] or [])
        before = sorted(current)
        new = sorted((current - set(remove)) | set(add))
        updated = tx.execute(
            _Q_PATCH_ROLES, dict(uuid=uuid, roles=new),
        ).fetchone()
        record_audit(
            tx, s, 'patch_roles',
            target_email=target_email, target_uuid=uuid,
            metadata={'before': before, 'after': new, 'reason': req.reason},
        )
    return {'ok': True, 'roles': list(updated['roles'])}


# ----- LIFECYCLE -----------------------------------------------------------

@apost('/admin/users/<uuid>/deactivate')
@validate(t.PostLifecycle)
def post_deactivate(req: t.PostLifecycle, s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx() as tx:
        row = tx.execute(_Q_SOFT_DELETE, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        record_audit(
            tx, s, 'deactivate',
            target_email=row['email'], target_uuid=uuid,
            metadata={'reason': req.reason},
        )
    return {'ok': True}


@apost('/admin/users/<uuid>/reactivate')
@validate(t.PostLifecycle)
def post_reactivate(req: t.PostLifecycle, s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx() as tx:
        row = tx.execute(_Q_REACTIVATE, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        record_audit(
            tx, s, 'reactivate',
            target_email=row['email'], target_uuid=uuid,
            metadata={'reason': req.reason},
        )
    return {'ok': True}


@adelete('/admin/users/<uuid>')
@validate(t.DeletePerson)
def delete_person(req: t.DeletePerson, s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx() as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        if (row['email'] or '').strip().lower() != req.confirm_email.strip().lower():
            # Hard delete is irreversible — the safety gate is a string
            # match against the stored email, so a typo or stale UI state
            # can't cascade-wipe the wrong row. FK cascade handles photos,
            # likes, messages, sessions, etc.
            abort(400)
        del_row = tx.execute(_Q_HARD_DELETE, dict(uuid=uuid)).fetchone()
        record_audit(
            tx, s, 'hard_delete',
            target_email=del_row['email'], target_uuid=uuid,
            metadata={'reason': req.reason},
        )
    return {'ok': True}


# ----- SUPPORT / DEBUG -----------------------------------------------------

@apost('/admin/users/<uuid>/clear-onboardee')
@validate(t.PostLifecycle)
def post_clear_onboardee(req: t.PostLifecycle, s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx() as tx:
        row = tx.execute(_Q_CLEAR_ONBOARDEE, dict(uuid=uuid)).fetchone()
        record_audit(
            tx, s, 'clear_onboardee',
            target_email=(row or {}).get('email'), target_uuid=uuid,
            metadata={'reason': req.reason},
        )
    return {'ok': True}


@apost('/admin/users/<uuid>/resend-otp')
@validate(t.PostLifecycle)
def post_resend_otp_admin(req: t.PostLifecycle, s: t.SessionInfo, uuid: str):
    require_admin(s)
    with api_tx() as tx:
        row = tx.execute(_Q_PERSON_BY_UUID, dict(uuid=uuid)).fetchone()
        if row is None:
            abort(404)
        target_email = row['email']
        session_token = secrets.token_hex(64)
        session_token_hash = sha512(session_token)
        # Q_INSERT_DUO_SESSION runs the standard _OTP_CTE (generates a
        # 6-digit OTP, applies banned/bad-domain checks) and INSERTs one
        # duo_session row — same shape used by post_request_otp.
        rows = tx.execute(
            Q_INSERT_DUO_SESSION,
            dict(
                email=target_email,
                normalized_email=normalize_email(target_email),
                pending_club_name=None,
                is_dev=False,
                session_token_hash=session_token_hash,
                ip_address=request.remote_addr or '127.0.0.1',
            ),
        ).fetchall()
        otp = None
        if rows:
            otp = rows[0].get('otp')
        record_audit(
            tx, s, 'resend_otp',
            target_email=target_email, target_uuid=uuid,
            metadata={'reason': req.reason, 'sent': otp is not None},
        )
    # Send the email AFTER the tx commits so a mail-send failure can't
    # roll back the duo_session row the user will need to redeem the OTP.
    if otp:
        try:
            from service.person import _send_otp
            _send_otp(target_email, otp)
        except Exception:
            # Non-fatal — admin can retry; the row is in duo_session.
            pass
    return {'ok': True, 'sent': bool(otp)}
