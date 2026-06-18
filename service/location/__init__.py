from database import api_tx
import json
import os
from typing import Optional
from functools import lru_cache

_locations_json_file = os.path.join(
        os.path.dirname(__file__), '..', '..',
        'locations', 'locations.json')

Q_SEARCH_LOCATIONS = """
SELECT
    long_friendly
FROM
    location
WHERE
    long_friendly ILIKE %(first_character)s || '%%'
ORDER BY
    long_friendly <-> %(search_string)s
LIMIT 10
"""

def init_db():
    with open(_locations_json_file) as f:
        locations = json.load(f)

    with api_tx() as tx:
        tx.execute("SELECT COUNT(*) FROM location")
        if tx.fetchone()['count'] != 0:
            return

        tx.executemany(
            """
            INSERT INTO Location (
                short_friendly,
                long_friendly,
                city,
                subdivision,
                country,
                coordinates,
                verification_required
            ) VALUES (
                %(short_friendly)s,
                %(long_friendly)s,
                %(city)s,
                %(subdivision)s,
                %(country)s,
                ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
                %(verification_required)s
            ) ON CONFLICT DO NOTHING
            """,
            locations
        )

@lru_cache(maxsize=26**3)
def get_search_locations(q: Optional[str]):
    if q is None:
        return []

    normalized_whitespace = ' '.join(q.split())

    if len(normalized_whitespace) < 1:
        return []

    params = dict(
        first_character=normalized_whitespace[0],
        search_string=normalized_whitespace,
    )

    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_SEARCH_LOCATIONS, params)
        return [row['long_friendly'] for row in tx.fetchall()]

Q_COUNTRY_LOCATION = """
SELECT long_friendly
FROM location
WHERE country = %(country)s
ORDER BY coordinates::geometry <-> (
    SELECT ST_Centroid(ST_Collect(coordinates::geometry))
    FROM location
    WHERE country = %(country)s
)
LIMIT 1
"""

def get_country_location(cc: Optional[str]):
    """ISO2 country code -> one representative in-country long_friendly.

    Exact location.country match (via pycountry name), so it can never
    return a same-named city in a different country the way the trigram
    autocomplete (get_search_locations) can. Returns a 0- or 1-element
    list mirroring get_search_locations' shape. The caller falls back to
    the autocomplete when this is empty (country not in the location
    table / pycountry name mismatch)."""
    try:
        import pycountry
        rec = pycountry.countries.get(alpha_2=(cc or '').strip().upper())
    except Exception:
        rec = None
    if not rec:
        return []
    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_COUNTRY_LOCATION, dict(country=rec.name))
        return [row['long_friendly'] for row in tx.fetchall()]


Q_NEAREST_LOCATION = """
SELECT long_friendly
FROM location
ORDER BY coordinates <-> ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography
LIMIT 1
"""

def get_nearest_location(lat, lng):
    """lat/lng (e.g. from the browser's geolocation) -> the nearest gazetteer
    long_friendly. Returns a 0- or 1-element list mirroring
    get_search_locations / get_country_location. Snapping to the nearest city
    keeps placement at city-level (never an exact address)."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return []
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
        return []
    with api_tx('READ COMMITTED') as tx:
        tx.execute(Q_NEAREST_LOCATION, dict(lat=lat_f, lng=lng_f))
        row = tx.fetchone()
        return [row['long_friendly']] if row else []
