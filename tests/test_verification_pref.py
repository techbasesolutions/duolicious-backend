"""verification_required conflation fix (2026-05-21).

The 'require verified matches' privacy preference must write
require_verified_prospects, NOT the anti-abuse person.verification_required
(which the chat send-gate + global search hide read). These tests pin that
separation so enabling the preference can't block the user's own messaging.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


def _make_person(tx):
    return tx.execute(
        """
        INSERT INTO person (
            email, normalized_email, name, date_of_birth,
            coordinates, gender_id, about,
            location_short_friendly, location_long_friendly, unit_id
        ) VALUES (
            %(e)s, %(e)s, 'T', '1990-01-01',
            ST_SetSRID(ST_MakePoint(0,0),4326)::geography,
            (SELECT id FROM gender LIMIT 1), 'a', 's', 's, s',
            (SELECT id FROM unit LIMIT 1)
        ) RETURNING uuid::text AS uuid, id
        """,
        dict(e=f'vp-{uuid4()}@example.com'),
    ).fetchone()


@pytest.fixture
def person_row():
    from database import api_tx
    with api_tx() as tx:
        p = _make_person(tx)
    yield p
    with api_tx() as tx:
        tx.execute("DELETE FROM person WHERE id = %s", (p['id'],))


def test_preference_writes_new_column_not_antiabuse_flag(person_row):
    from database import api_tx
    import duotypes as t
    from service.person import patch_profile_info

    # patch_profile_info only reads s.person_id; a SimpleNamespace avoids
    # constructing the full Pydantic SessionInfo (email/session_token_hash/...).
    s = SimpleNamespace(person_id=person_row["id"], person_uuid=person_row["uuid"])
    patch_profile_info(t.PatchProfileInfo(verification_required="Yes"), s)

    with api_tx() as tx:
        row = tx.execute(
            "SELECT require_verified_prospects, verification_required "
            "FROM person WHERE id = %s",
            (person_row['id'],),
        ).fetchone()
    # Preference set on the new column...
    assert row['require_verified_prospects'] is True
    # ...and the anti-abuse flag is untouched, so the chat send-gate is not
    # triggered for a user who only enabled the preference.
    assert row['verification_required'] is False
