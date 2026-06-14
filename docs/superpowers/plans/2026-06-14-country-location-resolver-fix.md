# Country-Location Resolver Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A user's country selection must show them in the right place on the map — both at onboarding AND when they change their country later — by keeping `person.country` (ISO2) and `person.coordinates` consistent.

**Architecture:** One shared idea — resolve an ISO2 country to a representative *in-country* location by exact `location.country` match (guaranteed in-country, unlike the trigram autocomplete). Use it in two places: (1) a new `/country-location?cc=XX` endpoint the onboarding country step calls; (2) the post-onboarding `country` field handler, so a country change moves coordinates too.

**Tech Stack:** Flask + psycopg (ahavah-api, branch `ahavah/main`, deploys via GHA on push), Next 16 (ahavah-web, branch `master`, Vercel-on-push), PostgreSQL (location table), pycountry (already a dep).

---

## Root Cause (investigated — do not re-litigate)

- The map ([src/app/map/page.tsx:282-291](../../../../ahavah-web/src/app/map/page.tsx)) **drops any candidate without a `country` ISO** and positions markers by **stored lat/lng**. So "shows on the map" needs a non-empty `country` AND in-country `coordinates`.
- **Onboarding bug:** the country step ([src/app/onboarding/country/page.tsx:37-48](../../../../ahavah-web/src/app/onboarding/country/page.tsx)) resolves a country to a city via `/search-locations` (a trigram *city autocomplete*), then `res.find(endsWith) ?? res[0]`. For small Caribbean nations no in-country city ranks in the top 10 (verified: `Antigua and Barbuda` → Spain/Guatemala/Guadeloupe…), so it silently lands on a same-named city elsewhere ("St. Lucia, Malta", "Antigua Guatemala, Guatemala"). person.country is derived from that wrong location at finish-onboarding → wrong country.
- **Edit bug:** the post-onboarding `country` field handler ([service/person/__init__.py:1677-1690](../../service/person/__init__.py)) does **only** `SET country = UPPER(field_value)` — it never moves coordinates. Changing your country moves your /search pool but leaves your map marker stranded in the old country.

Affected prod users (Shemele→AG, Jem→LC, Lily→LC) already corrected in the DB; this prevents recurrence.

## Why ISO-keyed (not name-keyed) and no label rename

Both entry points already have the ISO2 (`cc`): onboarding calls `update({country: cc})`; profile-edit sends `country: <ISO>`. Keying the resolver on `cc` → `pycountry.countries.get(alpha_2=cc).name` → exact `location.country` match means the frontend display labels ("St. Lucia") are irrelevant to resolution, so **no label rename is needed**. Countries whose pycountry name doesn't match the DB seed (rare, e.g. accented "Saint Barthélemy") simply fall through to the existing behavior — never worse than today.

## File Structure

- `service/location/__init__.py` — add `Q_COUNTRY_LOCATION` + `get_country_location(cc)`.
- `service/api/__init__.py` — register `GET /country-location` (after `/search-locations`, line 253).
- `service/person/__init__.py` — rewrite the `country` field handler (1677-1690) to move coordinates.
- `tests/test_location_resolver.py` — **new** resolver + endpoint tests.
- `src/app/onboarding/country/page.tsx` (web) — resolve by `cc` first, trigram fallback.

---

### Task 1: Backend ISO-anchored resolver + endpoint

**Files:** Modify `service/location/__init__.py` (append ~line 73); `service/api/__init__.py:253`; create `tests/test_location_resolver.py`.

- [ ] **Step 1 — failing test** (`tests/test_location_resolver.py`):

```python
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
```

- [ ] **Step 2 — run, expect fail:** `pytest tests/test_location_resolver.py -v` → `ImportError: cannot import name 'get_country_location'`.

- [ ] **Step 3 — implement** (append to `service/location/__init__.py`):

```python
Q_COUNTRY_LOCATION = """
SELECT long_friendly
FROM location
WHERE country = %(country)s
ORDER BY long_friendly
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
```

And register the route in `service/api/__init__.py` after `get_search_locations` (line 253):

```python
@aget(
    '/country-location',
    expected_onboarding_status=None,
    expected_sign_in_status=None,
)
def get_country_location(_):
    return location.get_country_location(cc=request.args.get('cc'))
```

- [ ] **Step 4 — run, expect pass:** `pytest tests/test_location_resolver.py -v`

- [ ] **Step 5 — commit:** `feat(location): ISO-anchored /country-location resolver`

---

### Task 2: Country field handler moves coordinates

**Files:** Modify `service/person/__init__.py:1677-1690`.

- [ ] **Step 1 — replace the `country` handler body:**

```python
    elif field_name == 'country':
        # 2026-06-14: a country change must also move the user's map
        # marker. The map drops users with no country ISO and positions
        # markers by coordinates, so setting the ISO alone strands the
        # marker in the old country. Resolve a representative in-country
        # location (exact location.country match via pycountry) and move
        # coordinates + display strings together. Falls back to ISO-only
        # when the country isn't in the location table (coordinates kept).
        _iso = (field_value or '').strip().upper()
        _country_name = None
        if len(_iso) == 2 and _iso.isalpha():
            try:
                import pycountry
                _rec = pycountry.countries.get(alpha_2=_iso)
                _country_name = _rec.name if _rec else None
            except Exception:
                _country_name = None
        if _country_name:
            params = dict(person_id=s.person_id, iso=_iso, country_name=_country_name)
            q1 = """
            UPDATE person
            SET country = %(iso)s,
                coordinates = COALESCE(loc.coordinates, person.coordinates),
                location_short_friendly =
                    COALESCE(loc.short_friendly, person.location_short_friendly),
                location_long_friendly =
                    COALESCE(loc.long_friendly, person.location_long_friendly)
            FROM (SELECT 1) AS d
            LEFT JOIN (
                SELECT coordinates, short_friendly, long_friendly
                FROM location
                WHERE country = %(country_name)s
                ORDER BY long_friendly
                LIMIT 1
            ) AS loc ON TRUE
            WHERE person.id = %(person_id)s
            """
        else:
            params = dict(person_id=s.person_id, field_value=field_value)
            q1 = """
            UPDATE person
               SET country = UPPER(%(field_value)s)
             WHERE id = %(person_id)s
               AND length(%(field_value)s) = 2
               AND %(field_value)s ~ '^[A-Za-z]{2}$'
            """
```

(`params` is set in both branches; the `LEFT JOIN (SELECT 1) AS d ... ON TRUE` guarantees the row exists even when no in-country location is found, so the ISO always sets while coordinates only move when a location resolves.)

- [ ] **Step 2 — verify app still boots:** `pytest tests/ -k "health or location" -v`

- [ ] **Step 3 — commit:** `fix(person): country change moves map coordinates too`

---

### Task 3: Onboarding resolves by ISO first

**Files:** Modify `src/app/onboarding/country/page.tsx` (web).

- [ ] **Step 1 — resolver + call site.** Change `resolveLongFriendly` to try the ISO endpoint first, keep the trigram as fallback:

```ts
async function resolveLongFriendly(
  cc: string,
  countryName: string,
): Promise<string | null> {
  // ISO-anchored first: the server resolves a representative in-country
  // location (exact country match), so we never land on a same-named city
  // in another country ("St. Lucia, Malta" for Caribbean Saint Lucia).
  try {
    const exact = await apiClient.get<string[]>(
      `/country-location?cc=${encodeURIComponent(cc)}`,
    );
    if (exact[0]) return exact[0];
  } catch {
    // fall through to the autocomplete resolver
  }
  // Fallback: trigram autocomplete (covers countries pycountry/the DB don't
  // map cleanly). Preserves pre-fix behavior so no country is worse off.
  try {
    const res = await apiClient.get<string[]>(
      `/search-locations?q=${encodeURIComponent(countryName)}`,
    );
    return (
      res.find((s) => s.toLowerCase().endsWith(countryName.toLowerCase())) ??
      res[0] ??
      null
    );
  } catch {
    return null;
  }
}
```

Update the call site (currently `resolveLongFriendly(country.name)`):

```ts
const longFriendly = await resolveLongFriendly(cc, country.name);
```

- [ ] **Step 2 — typecheck + build:** project typecheck/build scripts, expect PASS.

- [ ] **Step 3 — commit:** `fix(onboarding): resolve location by country ISO, trigram fallback`

---

### Task 4: Deploy + verification (success criteria)

- [ ] **Backend deploy** — push `ahavah/main`; GHA runs migrations + pytest + rebuild. Confirm pytest stage green.
- [ ] **Live endpoint:** `curl -s https://api.ahavah.app/country-location?cc=LC` and `?cc=AG` → 1-element array ending in the country name.
- [ ] **Onboarding E2E:** throwaway signup, pick **Saint Lucia** → finish → DB shows `country='LC'` + coordinates in Saint Lucia (not Malta). Repeat **Antigua** → `AG`. Delete throwaways.
- [ ] **Edit E2E:** take a test person, PATCH `country` to a new country (e.g. via profile edit) → DB shows new ISO **and** coordinates moved into the new country; confirm they render on the map there. Restore the test user afterward.

## Known simplification (beta-acceptable)

`Q_COUNTRY_LOCATION` uses `ORDER BY long_friendly LIMIT 1` — alphabetically-first in-country city, not necessarily the capital. Correct for the map pin + country badge; displayed town may be a minor locality. The `location` table has no capital/population column to rank on. Logged, not silently capped.
