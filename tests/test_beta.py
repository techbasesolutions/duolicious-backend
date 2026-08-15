"""Beta-tester cohort (2026-05-26) - service.beta tests."""

from __future__ import annotations

from uuid import uuid4


def test_register_is_idempotent_and_counts():
    from database import api_tx
    from service.beta import register, count
    email = f"beta-{uuid4()}@example.com"
    with api_tx() as tx:
        before = count(tx)
        assert register(tx, email, None) == (True, False)   # brand-new row
        assert count(tx) == before + 1
        assert register(tx, email, None) == (False, False)  # repeat opt-in, no-op
        assert count(tx) == before + 1
        tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=email))


def test_register_normalizes_email():
    from database import api_tx
    from service.beta import register
    raw = f"  BETA-{uuid4()}@Example.com  "
    norm = raw.strip().lower()
    with api_tx() as tx:
        assert register(tx, raw, None) == (True, False)
        # Re-register under the normalized key is a no-op (same row).
        assert register(tx, norm, None) == (False, False)
        tx.execute("DELETE FROM beta_signup WHERE email = %(e)s", dict(e=norm))
