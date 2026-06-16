"""Launch claim flow.

A signed `claim` token (emailed to a waitlist registrant) logs them in and
pre-fills an onboardee from their waitlist answers, so they land in onboarding
with demographics already filled. The token IS the auth -- no OTP. Caller owns
the api_tx.

Mirrors the onboarding write paths in service/person.patch_onboardee_info
(onboardee upserts keyed ON CONFLICT (email)) and the programmatic session mint
in service/api/admin/users_action_routes (token_hex(64) + sha512 -> duo_session).
"""
from __future__ import annotations

import json
import secrets

from duohash import sha512
from service.unsubscribe import parse_token


# Waitlist `sex` answers are lowercase free text (male/female); gender.name is
# title-case (Man/Woman). Map across; unknown values just skip the pre-fill.
_SEX_TO_GENDER = {"male": "Man", "female": "Woman"}

# Demographic answer keys that round-trip through onboardee.ahavah_extra (the
# same blob /finish-onboarding copies onto the person). Best-effort: the
# onboarding wizard reads the keys it recognizes; unknown keys are ignored, so
# pre-filling is lossless and never blocks finishing onboarding.
_AHAVAH_EXTRA_KEYS = ("assembly", "intent", "family")


_Q_PERSON_BY_EMAIL = "SELECT id FROM person WHERE normalized_email = %(email)s"

_Q_ENSURE_ONBOARDEE = """
    INSERT INTO onboardee (email) VALUES (%(email)s)
    ON CONFLICT (email) DO NOTHING
"""

_Q_SET_GENDER = """
    INSERT INTO onboardee (email, gender_id)
    SELECT %(email)s, id FROM gender WHERE name = %(gender)s
    ON CONFLICT (email) DO UPDATE SET gender_id = EXCLUDED.gender_id
"""

_Q_SET_COORDS = """
    INSERT INTO onboardee (email, coordinates)
    SELECT %(email)s, coordinates FROM location
    WHERE country = %(country_name)s
    -- Prefer a location in the waitlist `region` (state/province) so people
    -- place on the map by state rather than all stacking on the country's
    -- first-alphabetical city. Falls back to that city when region is blank
    -- or doesn't resolve.
    ORDER BY
      CASE WHEN %(region)s <> ''
                AND long_friendly ILIKE '%%, ' || %(region)s || ', %%'
           THEN 0 ELSE 1 END,
      long_friendly
    LIMIT 1
    ON CONFLICT (email) DO UPDATE SET coordinates = EXCLUDED.coordinates
"""

_Q_MERGE_EXTRA = """
    INSERT INTO onboardee (email, ahavah_extra)
    VALUES (%(email)s, %(blob)s::jsonb)
    ON CONFLICT (email) DO UPDATE SET
        ahavah_extra = onboardee.ahavah_extra || EXCLUDED.ahavah_extra
"""

# Direct signed-in session insert -- the claim token already authenticated the
# recipient, so we skip the OTP CTE entirely. otp is NOT NULL; a used/expired
# placeholder satisfies the column without being usable.
_Q_MINT_SESSION = """
    INSERT INTO duo_session (session_token_hash, person_id, email, otp, signed_in)
    VALUES (%(hash)s, %(person_id)s, %(email)s, '000000', TRUE)
"""


def _country_name(cc):
    """ISO2 -> pycountry country name (matches location.country), or None."""
    try:
        import pycountry
        rec = pycountry.countries.get(alpha_2=(cc or "").strip().upper())
        return rec.name if rec else None
    except Exception:
        return None


def claim(tx, token: str) -> dict | None:
    """Resolve a `claim` token to a signed-in session.

    On first use, creates + pre-fills an onboardee from the email's waitlist
    answers. Idempotent: an existing person is just re-logged-in (no duplicate
    onboardee). Returns {"session_token": <128-hex>, "onboarded": bool}, or
    None if the token is invalid / not a claim token.
    """
    parsed = parse_token(token)
    if not parsed:
        return None
    scope, email = parsed
    if scope != "claim":
        return None

    row = tx.execute(_Q_PERSON_BY_EMAIL, dict(email=email)).fetchone()
    person_id = row["id"] if row else None

    if person_id is None:
        # First claim: create + pre-fill the onboardee from waitlist answers.
        from service.waitlist import get as _waitlist_get
        wl = _waitlist_get(tx, email)
        answers = (wl or {}).get("answers") or {}

        tx.execute(_Q_ENSURE_ONBOARDEE, dict(email=email))

        gender = _SEX_TO_GENDER.get(str(answers.get("sex", "")).strip().lower())
        if gender:
            tx.execute(_Q_SET_GENDER, dict(email=email, gender=gender))

        country_name = _country_name(answers.get("country"))
        if country_name:
            region = str(answers.get("region") or "").strip()
            tx.execute(_Q_SET_COORDS, dict(
                email=email, country_name=country_name, region=region))

        extra = {k: answers[k] for k in _AHAVAH_EXTRA_KEYS if answers.get(k)}
        if extra:
            tx.execute(_Q_MERGE_EXTRA, dict(email=email, blob=json.dumps(extra)))

    session_token = secrets.token_hex(64)
    tx.execute(_Q_MINT_SESSION, dict(
        hash=sha512(session_token),
        person_id=person_id,
        email=email,
    ))
    return {"session_token": session_token, "onboarded": person_id is not None}
