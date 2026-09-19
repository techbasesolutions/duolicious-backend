"""`person.country` -> the country name a member reads.

`person.country` holds an ISO 3166-1 alpha-2 code (US, CA, BB, GB, NG), which
is what the search filters and the onboarding map want and exactly what no
member, operator or follower should ever be shown. Spotlight used to pass the
column straight through, so a welcome card read "BB" under the member's name
and its caption read "Welcome to Ahavah, Ehud. BB."

`display_country` is the one place that mapping happens. It is deliberately
total: anything that is not a two-letter alphabetic code, any code ISO does
not know, and a missing pycountry all come back unchanged, so the same helper
can be applied to a column that is sometimes already a human string (the
`COALESCE(p.country, p.location_short_friendly)` reads fall back to
"Warrens, Barbados", which must survive untouched). It never raises: a member
looking at their own card must not get a 500 because of a spelling.

Names come from pycountry (already a dependency, used by the location and
claim resolvers), preferring `common_name` over the formal `name`, since ISO
records "Bolivia, Plurinational State of" where a member says "Bolivia".
`_OVERRIDES` pins the handful whose everyday name is not derivable from the
ISO record, and everything else with a formal tail is cut at the first comma.
"""
from __future__ import annotations

from typing import Optional

# Pinned rather than derived: each of these is either a name pycountry gives
# no short form for (RU, CD) or one whose short form we refuse to let drift
# with a pycountry release, since it is the member's own country line.
_OVERRIDES = {
    'KR': 'South Korea',
    'KP': 'North Korea',
    'TZ': 'Tanzania',
    'MD': 'Moldova',
    'IR': 'Iran',
    'RU': 'Russia',
    'SY': 'Syria',
    'LA': 'Laos',
    'CD': 'DR Congo',
    'PS': 'Palestine',
    'TW': 'Taiwan',
    'FM': 'Micronesia',
    'VN': 'Vietnam',
    'US': 'United States',
    'GB': 'United Kingdom',
}


def display_country(value: Optional[str]) -> Optional[str]:
    """The short, natural name for an alpha-2 country code.

    Anything else (None, a name already, an unknown code, a non-string) comes
    back exactly as it went in.
    """
    try:
        if value is None or not isinstance(value, str):
            return value
        code = value.strip()
        if len(code) != 2 or not code.isalpha():
            return value
        code = code.upper()
        override = _OVERRIDES.get(code)
        if override:
            return override
        # Imported here rather than at module load, matching the other two
        # pycountry call sites (service/person/__init__.py): the dependency is
        # optional to this function by design, and a Spotlight read must not
        # fail to import because of it.
        import pycountry
        record = pycountry.countries.get(alpha_2=code)
        if record is None:
            return value
        name = getattr(record, 'common_name', None) or getattr(record, 'name', None)
        if not name:
            return value
        # "Korea, Republic of", "Palestine, State of": ISO puts the formal
        # qualifier after a comma, and the part before it is the name people
        # use. Overrides above win before this ever runs.
        if ', ' in name:
            name = name.split(', ', 1)[0]
        return name
    except Exception:
        return value
