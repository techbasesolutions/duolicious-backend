"""Admin deactivate must wipe the target's sessions — a deactivated user
shouldn't keep authenticated access (require_auth's session lookup doesn't
check person.activated). Tests _Q_SOFT_DELETE directly; the admin HTTP
auth layer is covered by require_admin's own tests."""
from __future__ import annotations

from uuid import uuid4


def _insert_person():
    from database import api_tx
    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO person (
                email, normalized_email, name, date_of_birth,
                coordinates, gender_id, about, location_short_friendly, location_long_friendly, unit_id
            ) VALUES (
                %(email)s, %(email)s, 'Test', '1990-01-01',
                ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                (SELECT id FROM gender LIMIT 1), 'about', 'somewhere', 'somewhere, nowhere', (SELECT id FROM unit LIMIT 1)
            ) RETURNING uuid::text AS uuid, id, email
            """,
            dict(email=f'deact-{uuid4()}@example.com'),
        ).fetchone()
    return row


def test_soft_delete_wipes_sessions_and_deactivates():
    from database import api_tx
    from service.api.admin.users_action_routes import _Q_SOFT_DELETE

    p = _insert_person()
    try:
        with api_tx() as tx:
            tx.execute(
                "INSERT INTO duo_session "
                "(session_token_hash, email, person_id, signed_in, otp) "
                "VALUES (%s, %s, %s, TRUE, '123456')",
                (f'hash-{p["uuid"]}', p['email'], p['id']),
            )

        # Sanity: the session exists before deactivation.
        with api_tx() as tx:
            assert tx.execute(
                "SELECT 1 FROM duo_session WHERE person_id = %s", (p['id'],)
            ).fetchone() is not None

        with api_tx() as tx:
            row = tx.execute(_Q_SOFT_DELETE, dict(uuid=p['uuid'])).fetchone()
            assert row['email'] == p['email']

        with api_tx() as tx:
            # Session wiped...
            assert tx.execute(
                "SELECT 1 FROM duo_session WHERE person_id = %s", (p['id'],)
            ).fetchone() is None
            # ...and the person is deactivated.
            assert tx.execute(
                "SELECT activated FROM person WHERE id = %s", (p['id'],)
            ).fetchone()['activated'] is False
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM person WHERE id = %s", (p['id'],))
