"""Country-anchored location resolver (regression guard, 2026-06-14).

Picking a small Caribbean country during onboarding used to resolve to a
same-named city in the wrong country (Saint Lucia -> Malta, Antigua and
Barbuda -> Guatemala) because /search-locations is a trigram autocomplete,
not a country resolver."""
from __future__ import annotations


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
    r = client.get('/country-location?cc=LC')
    assert r.status_code == 200
    assert r.get_json()[0].lower().endswith('saint lucia')
