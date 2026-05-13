"""
Phase 1 Task 1.3 — discovery preferences (country / language / long-distance).

Endpoints exposed via service/api/__init__.py:
  GET  /discovery-prefs   — read current settings + selected pref arrays
  PATCH /discovery-prefs  — partial-update of person.{country,region,languages_spoken,primary_language,auto_translate_enabled}
  POST /search-preference-country  — replace the user's preferred-countries set
  POST /search-preference-language — replace the user's preferred-languages set
  POST /search-preference-long-distance — set the open-to-long-distance flag

These are intentionally separate from duolicious's `patch_profile_info`
(which is field-driven via a Pydantic union type and would require touching
duotypes for every new field). Self-contained dict-driven handlers keep the
Q&A-removal blast radius minimal.
"""

from __future__ import annotations

from typing import Any
from flask import request
from database import api_tx


# ---------------------------------------------------------------------------
# GET /discovery-prefs
# ---------------------------------------------------------------------------

def get_discovery_prefs(s):
    if not s.person_id:
        return 'Not authorized', 401

    with api_tx('READ COMMITTED') as tx:
        row = tx.execute(
            """
            SELECT country, region, languages_spoken, primary_language,
                   auto_translate_enabled
            FROM person WHERE id = %(person_id)s
            """,
            dict(person_id=s.person_id),
        ).fetchone()
        if not row:
            return 'Person not found', 404

        countries = [r['country'] for r in tx.execute(
            'SELECT country FROM search_preference_country WHERE person_id = %(person_id)s',
            dict(person_id=s.person_id),
        ).fetchall()]

        languages = [r['language'] for r in tx.execute(
            'SELECT language FROM search_preference_language WHERE person_id = %(person_id)s',
            dict(person_id=s.person_id),
        ).fetchall()]

        long_distance_row = tx.execute(
            'SELECT open_to_long_distance FROM search_preference_open_to_long_distance WHERE person_id = %(person_id)s',
            dict(person_id=s.person_id),
        ).fetchone()
        open_to_long_distance = (
            long_distance_row['open_to_long_distance'] if long_distance_row else True
        )

    return {
        'country':                 row['country'],
        'region':                  row['region'],
        'languages_spoken':        row['languages_spoken'] or [],
        'primary_language':        row['primary_language'],
        'auto_translate_enabled':  row['auto_translate_enabled'],
        'preferred_countries':     countries,
        'preferred_languages':     languages,
        'open_to_long_distance':   open_to_long_distance,
    }


# ---------------------------------------------------------------------------
# PATCH /discovery-prefs
# ---------------------------------------------------------------------------

ALLOWED_PROFILE_FIELDS = {
    'country':                ('CHAR(2)',  lambda v: isinstance(v, str) and len(v) == 2),
    'region':                 ('TEXT',     lambda v: v is None or isinstance(v, str)),
    'languages_spoken':       ('TEXT[]',   lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)),
    'primary_language':       ('TEXT',     lambda v: isinstance(v, str)),
    'auto_translate_enabled': ('BOOLEAN',  lambda v: isinstance(v, bool)),
}


def patch_discovery_prefs(s):
    """Partial-update the user's profile-side discovery fields. Only fields
    whitelisted in ALLOWED_PROFILE_FIELDS are accepted; unknown fields 400."""
    if not s.person_id:
        return 'Not authorized', 401

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body:
        return 'Body must be a non-empty JSON object', 400

    set_clauses = []
    params: dict[str, Any] = dict(person_id=s.person_id)
    for key, value in body.items():
        if key not in ALLOWED_PROFILE_FIELDS:
            return f'Unknown field: {key}', 400
        _, validator = ALLOWED_PROFILE_FIELDS[key]
        if not validator(value):
            return f'Invalid value for {key}', 400
        set_clauses.append(f'{key} = %({key})s')
        params[key] = value

    sql = f"UPDATE person SET {', '.join(set_clauses)} WHERE id = %(person_id)s"

    with api_tx() as tx:
        tx.execute(sql, params)

    return get_discovery_prefs(s)


# ---------------------------------------------------------------------------
# POST /search-preference-country  (replaces the user's preferred-countries)
# ---------------------------------------------------------------------------

def post_search_preference_country(s):
    if not s.person_id:
        return 'Not authorized', 401

    body = request.get_json(silent=True) or {}
    countries = body.get('countries')
    if not isinstance(countries, list) or any(not isinstance(c, str) or len(c) != 2 for c in countries):
        return 'countries must be an array of ISO-3166-1 alpha-2 codes', 400

    # Cap so a malicious client can't set 10000 prefs
    if len(countries) > 250:
        return 'Too many countries (max 250)', 400

    with api_tx() as tx:
        tx.execute(
            'DELETE FROM search_preference_country WHERE person_id = %(person_id)s',
            dict(person_id=s.person_id),
        )
        for c in set(countries):
            tx.execute(
                'INSERT INTO search_preference_country (person_id, country) VALUES (%(person_id)s, %(country)s)',
                dict(person_id=s.person_id, country=c),
            )

    return {'preferred_countries': sorted(set(countries))}


# ---------------------------------------------------------------------------
# POST /search-preference-language
# ---------------------------------------------------------------------------

def post_search_preference_language(s):
    if not s.person_id:
        return 'Not authorized', 401

    body = request.get_json(silent=True) or {}
    languages = body.get('languages')
    if not isinstance(languages, list) or any(not isinstance(l, str) for l in languages):
        return 'languages must be an array of ISO-639-1 codes', 400

    if len(languages) > 50:
        return 'Too many languages (max 50)', 400

    with api_tx() as tx:
        tx.execute(
            'DELETE FROM search_preference_language WHERE person_id = %(person_id)s',
            dict(person_id=s.person_id),
        )
        for l in set(languages):
            tx.execute(
                'INSERT INTO search_preference_language (person_id, language) VALUES (%(person_id)s, %(language)s)',
                dict(person_id=s.person_id, language=l),
            )

    return {'preferred_languages': sorted(set(languages))}


# ---------------------------------------------------------------------------
# POST /search-preference-long-distance
# ---------------------------------------------------------------------------

def post_search_preference_long_distance(s):
    if not s.person_id:
        return 'Not authorized', 401

    body = request.get_json(silent=True) or {}
    open_to_long_distance = body.get('open_to_long_distance')
    if not isinstance(open_to_long_distance, bool):
        return 'open_to_long_distance must be a boolean', 400

    with api_tx() as tx:
        tx.execute(
            """
            INSERT INTO search_preference_open_to_long_distance (person_id, open_to_long_distance)
            VALUES (%(person_id)s, %(open_to_long_distance)s)
            ON CONFLICT (person_id) DO UPDATE
              SET open_to_long_distance = EXCLUDED.open_to_long_distance
            """,
            dict(person_id=s.person_id, open_to_long_distance=open_to_long_distance),
        )

    return {'open_to_long_distance': open_to_long_distance}
