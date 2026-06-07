"""Unit tests for service.admin gate logic.

Pure-logic tests over the SQL string + the require_admin / record_audit
function shapes. Integration of the actual /admin/* endpoints is
covered by per-route tests added in each subsequent phase."""
from __future__ import annotations


def test_q_is_admin_uses_named_parameter():
    from service.admin.queries import Q_IS_ADMIN
    assert "%(uuid)s" in Q_IS_ADMIN
    assert "format" not in Q_IS_ADMIN.lower()
    assert "f'" not in Q_IS_ADMIN


def test_q_insert_audit_has_all_columns():
    from service.admin.queries import Q_INSERT_AUDIT
    for col in [
        "actor_email", "actor_uuid", "action",
        "target_email", "target_uuid", "metadata",
    ]:
        assert f"%({col})s" in Q_INSERT_AUDIT


def test_is_admin_returns_false_for_empty_uuid():
    from service.admin import is_admin
    assert is_admin(tx=None, person_uuid="") is False
    assert is_admin(tx=None, person_uuid=None) is False
