"""Beta-tester referrals — Phase 1 surface.

Functions in this file are called from POST /beta-tester and POST
/request-otp to record attribution when a new signup arrives via a
`/i/<code>` link. Credit firing (credit_pending_for_{invitee,inviter}
+ _credit_one) lands in Phase 2 alongside the post_finish_onboarding
integration; the parent spec at
docs/superpowers/specs/2026-06-05-beta-referrals-design.md describes
the full surface.

Caller owns the api_tx; everything below takes a psycopg cursor `tx`
as the first arg and never opens its own transaction. This matches
the existing service.beta / service.waitlist convention."""
from __future__ import annotations

import secrets
from typing import Optional

# Crockford base32 — no I, L, O, U (eliminates digit-letter ambiguity
# when read aloud or scribbled on paper). 32^7 = 34 billion codes.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 7
_MAX_MINT_RETRIES = 3


class ReferralCodeCollision(Exception):
    """Raised after _MAX_MINT_RETRIES consecutive unique-violation
    failures minting a code. Should never happen at 32^7 = 34B keyspace
    with only ~15 codes in flight; operational signal that the alphabet
    or length needs bumping."""


def _normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _is_well_formed_code(code: Optional[str]) -> bool:
    if not code or not isinstance(code, str):
        return False
    if len(code) != _CODE_LENGTH:
        return False
    return all(c in _ALPHABET for c in code)


def _random_code() -> str:
    # secrets.choice for cryptographic randomness; not strictly required
    # for a public-facing share code, but cheap and aligns with the rest
    # of the codebase (session tokens, OTPs).
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


_Q_GET_EXISTING_CODE = """
    SELECT referral_code FROM beta_signup WHERE email = %(email)s
"""

_Q_SET_CODE = """
    UPDATE beta_signup
       SET referral_code = %(code)s
     WHERE email = %(email)s
       AND referral_code IS NULL
"""

_Q_INVITER_FROM_CODE = """
    SELECT email FROM beta_signup WHERE referral_code = %(code)s
"""

_Q_INSERT_REFERRAL = """
    INSERT INTO referral (inviter_email, invitee_email)
    VALUES (%(inviter_email)s, %(invitee_email)s)
    ON CONFLICT (invitee_email) DO NOTHING
    RETURNING id, inviter_email, invitee_email, status
"""


def mint_code(tx, email: str) -> Optional[str]:
    """Idempotent. Returns the beta tester's referral_code, generating
    + storing one if NULL. Returns None if the email isn't in
    beta_signup (caller decides what to do)."""
    norm = _normalize_email(email)
    row = tx.execute(_Q_GET_EXISTING_CODE, dict(email=norm)).fetchone()
    if row is None:
        return None  # not a beta tester
    if row["referral_code"]:
        return row["referral_code"]

    last_err: Optional[Exception] = None
    for _ in range(_MAX_MINT_RETRIES):
        code = _random_code()
        try:
            cur = tx.execute(_Q_SET_CODE, dict(email=norm, code=code))
            if cur.rowcount == 1:
                return code
            # rowcount=0 means someone else minted a code for this row
            # between our SELECT and UPDATE; re-read.
            row2 = tx.execute(_Q_GET_EXISTING_CODE, dict(email=norm)).fetchone()
            if row2 and row2["referral_code"]:
                return row2["referral_code"]
        except Exception as e:
            # Unique-violation on the partial index (rare); retry with
            # a fresh random code.
            last_err = e
    raise ReferralCodeCollision(
        f"could not mint referral_code for {norm!r} after "
        f"{_MAX_MINT_RETRIES} tries; last error: {last_err!r}"
    )


def attribute(
    tx,
    inviter_code: Optional[str],
    invitee_email: str,
) -> Optional[dict]:
    """Called when a new signup arrives via /i/<code>.

    - Returns None if inviter_code is None / malformed / not found.
    - Returns None if inviter_email == invitee_email (self-referral).
    - Else INSERTs a referral row ON CONFLICT (invitee_email) DO NOTHING.
      Returns the row dict iff inserted, None if invitee was already
      attributed to someone else (first attribution wins).

    Safe to call multiple times for the same invitee (idempotent via the
    UNIQUE constraint). Safe to call with bad inputs (returns None)."""
    if not _is_well_formed_code(inviter_code):
        return None

    invitee_norm = _normalize_email(invitee_email)
    if not invitee_norm:
        return None

    inviter_row = tx.execute(
        _Q_INVITER_FROM_CODE, dict(code=inviter_code)
    ).fetchone()
    if inviter_row is None:
        return None  # unknown code
    inviter_email = inviter_row["email"]

    if inviter_email == invitee_norm:
        return None  # self-referral, silently void

    row = tx.execute(
        _Q_INSERT_REFERRAL,
        dict(inviter_email=inviter_email, invitee_email=invitee_norm),
    ).fetchone()
    return dict(row) if row else None
