"""Waitlist capture (2026-05-20) - service.waitlist upsert tests."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def email():
    e = f'wl-{uuid4()}@example.com'
    yield e
    from database import api_tx
    with api_tx() as tx:
        tx.execute("DELETE FROM waitlist_signup WHERE email = %(e)s", dict(e=e))


def test_insert_then_update_is_idempotent(email):
    from database import api_tx
    from service.waitlist import upsert, get
    with api_tx() as tx:
        upsert(tx, email, {"sex": "male", "country": "US"})
        row = get(tx, email)
        assert row["answers"]["sex"] == "male"
        # Re-submit overwrites answers, keeps the single row.
        upsert(tx, email, {"sex": "female", "country": "BB", "family": "wants-children"})
        row2 = get(tx, email)
        assert row2["answers"]["sex"] == "female"
        assert row2["answers"]["family"] == "wants-children"
        cnt = tx.execute(
            "SELECT count(*) AS c FROM waitlist_signup WHERE email = %(e)s",
            dict(e=email),
        ).fetchone()["c"]
        assert cnt == 1


def test_email_is_normalized(email):
    from database import api_tx
    from service.waitlist import upsert, get
    with api_tx() as tx:
        upsert(tx, f"  {email.upper()}  ", {"sex": "male"})
        # Stored + fetchable under the normalized (lower, trimmed) key.
        assert get(tx, email) is not None


def test_count_reflects_rows(email):
    from database import api_tx
    from service.waitlist import upsert, count
    with api_tx() as tx:
        before = count(tx)
        upsert(tx, email, {"sex": "male"})
        after = count(tx)
        assert after == before + 1
