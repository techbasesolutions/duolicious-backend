"""Country-anchored location resolver (regression guard, 2026-06-14).

Picking a small Caribbean country during onboarding used to resolve to a
same-named city in the wrong country (Saint Lucia -> Malta, Antigua and
Barbuda -> Guatemala) because /search-locations is a trigram autocomplete,
not a country resolver."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def seed_caribbean_locations():
    """`location` is populated from a large external dataset the test
    database does not carry (0 rows locally vs ~126k in production), so
    these country-resolution tests could never pass here.

    Seeds only the three countries under test, and only IN-COUNTRY rows,
    which is exactly what the assertions require the resolver to pick.
    It therefore cannot mask the regression this file guards against
    (resolving to a same-named city in the WRONG country) — no
    out-of-country decoys are introduced. Idempotent.
    """
    from database import api_tx

    rows = [
        ('Castries, Saint Lucia', 'Castries', 'Saint Lucia', -60.99, 14.01),
        ("Saint John's, Antigua and Barbuda", "Saint John's",
         'Antigua and Barbuda', -61.85, 17.12),
        ('Basseterre, Saint Kitts and Nevis', 'Basseterre',
         'Saint Kitts and Nevis', -62.72, 17.30),
    ]
    with api_tx() as tx:
        for long_friendly, short_friendly, country, lon, lat in rows:
            tx.execute(
                """
                INSERT INTO location
                    (long_friendly, short_friendly, city, subdivision,
                     country, coordinates)
                VALUES (
                    %(lf)s, %(sf)s, %(sf)s, '', %(c)s,
                    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                )
                ON CONFLICT DO NOTHING
                """,
                dict(lf=long_friendly, sf=short_friendly, c=country,
                     lon=lon, lat=lat),
            )
    yield


def test_resolves_in_country_city_by_iso():
    from service.location import get_country_location
    for cc, suffix in {
        'LC': 'saint lucia',
        'AG': 'antigua and barbuda',
        'KN': 'saint kitts and nevis',
    }.items():
        res = get_country_location(cc)
        assert res, f"no location resolved for {cc}"
        assert res[0].lower().endswith(suffix), f"{cc} -> {res[0]!r} not in-country"


def test_unknown_or_blank_iso_returns_empty():
    from service.location import get_country_location
    assert get_country_location('ZZ') == []
    assert get_country_location(None) == []
    assert get_country_location('   ') == []


def test_country_location_endpoint(client):
    # The endpoint uses the default auth='required' (400 without a bearer
    # token). In production it is called mid-onboarding, when the client
    # already holds a session token — mirror that: an onboardee session
    # (person_id NULL) is enough because both expected statuses are None.
    import hashlib
    import secrets
    from uuid import uuid4

    from database import api_tx

    tok = secrets.token_hex(32)
    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO duo_session (session_token_hash, email, signed_in, otp)
            VALUES (%(h)s, %(e)s, TRUE, '123456')
            """,
            dict(
                h=hashlib.sha512(tok.encode()).hexdigest(),
                e=f'loc-resolver-{uuid4()}@example.com',
            ),
        )

    r = client.get(
        '/country-location?cc=LC',
        headers={'Authorization': f'Bearer {tok}'},
    )
    assert r.status_code == 200
    assert r.get_json()[0].lower().endswith('saint lucia')
