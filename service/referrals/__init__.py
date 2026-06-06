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
        # SAVEPOINT wraps each attempt so a unique-violation does not
        # poison the outer tx (psycopg leaves the tx in a failed state
        # until ROLLBACK, after which all further commands are skipped
        # with "current transaction is aborted"). The caller may be
        # mid-flight in a multi-statement api_tx (e.g. the CLI backfill
        # loop in emails/send_referral_intro.py), so we must keep that
        # tx alive across collisions.
        tx.execute("SAVEPOINT mint_code_attempt")
        try:
            cur = tx.execute(_Q_SET_CODE, dict(email=norm, code=code))
            if cur.rowcount == 1:
                tx.execute("RELEASE SAVEPOINT mint_code_attempt")
                return code
            # rowcount=0 → someone else minted a code for this row
            # between our SELECT and UPDATE. Re-read and return.
            tx.execute("RELEASE SAVEPOINT mint_code_attempt")
            row2 = tx.execute(_Q_GET_EXISTING_CODE, dict(email=norm)).fetchone()
            if row2 and row2["referral_code"]:
                return row2["referral_code"]
        except Exception as e:
            # Unique-violation on the partial index (rare; 34B keyspace
            # vs ~20 codes in flight). Roll back this attempt's savepoint
            # so the outer tx stays usable, then retry with a fresh
            # random code.
            tx.execute("ROLLBACK TO SAVEPOINT mint_code_attempt")
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
    # service.beta.register stores emails normalized, but service.referrals
    # does not own that invariant. Normalize defensively so the self-
    # referral check is robust against any future writer that bypasses
    # service.beta.
    inviter_email = _normalize_email(inviter_row["email"])

    if inviter_email == invitee_norm:
        return None  # self-referral, silently void

    row = tx.execute(
        _Q_INSERT_REFERRAL,
        dict(inviter_email=inviter_email, invitee_email=invitee_norm),
    ).fetchone()
    return dict(row) if row else None


_Q_ALREADY_CREDITED = """
    SELECT 1 FROM token_ledger
     WHERE reason = 'referral'
       AND metadata->>'referral_id' = %(referral_id)s
     LIMIT 1
"""


def _credit_one(tx, person_uuid: str, referral_id: str) -> bool:
    """Idempotent +5 token credit. Returns True if a new ledger row was
    inserted, False if a row already exists for this referral_id (safe
    no-op, never should happen but defends against concurrent /finish-
    onboarding races on the same email).

    Caller is responsible for flipping the referral row to
    status='credited' AFTER this returns True."""
    if tx.execute(
        _Q_ALREADY_CREDITED, dict(referral_id=referral_id)
    ).fetchone() is not None:
        return False

    # Avoid a circular import — service.tokens depends on nothing in
    # service.referrals but the inverse needs late binding.
    from service.tokens import credit
    credit(
        tx,
        person_uuid,
        5,
        reason="referral",
        metadata={"referral_id": referral_id},
    )
    return True


_Q_FLIP_INVITEE_GRADUATED = """
    UPDATE referral
       SET status = 'graduated', graduated_at = NOW()
     WHERE invitee_email = %(invitee_email)s
       AND status = 'pending'
    RETURNING id, inviter_email
"""

_Q_INVITER_PERSON_UUID = """
    SELECT uuid::TEXT AS uuid FROM person
     WHERE normalized_email = %(email)s
        OR email = %(email)s
     LIMIT 1
"""

_Q_FLIP_GRADUATED_TO_CREDITED = """
    UPDATE referral
       SET status = 'credited', credited_at = NOW()
     WHERE id = %(id)s
       AND status = 'graduated'
    RETURNING id
"""


def credit_pending_for_invitee(tx, invitee_email: str) -> Optional[str]:
    """Called at the invitee's POST /finish-onboarding inside the same
    api_tx that creates their person row. Flips their referral row
    pending → graduated, then if their inviter ALSO has a person row,
    fires the inviter's +5 credit and flips graduated → credited.

    Returns the inviter's person.uuid iff a row was matched (whether
    or not a credit fired this call — the credit may have already been
    placed by an earlier graduation race). Returns None if no referral
    row exists for this invitee, or if the inviter hasn't graduated yet.

    NOTE: takes invitee_email only — the credit goes to the INVITER,
    looked up from referral.inviter_email. The earlier signature also
    took invitee_person_uuid but it was unused; dropped 2026-06-06."""
    norm = _normalize_email(invitee_email)
    row = tx.execute(
        _Q_FLIP_INVITEE_GRADUATED, dict(invitee_email=norm)
    ).fetchone()
    if row is None:
        return None  # nothing pending for this invitee

    referral_id = str(row["id"])
    inviter_email = row["inviter_email"]
    inviter_row = tx.execute(
        _Q_INVITER_PERSON_UUID, dict(email=inviter_email)
    ).fetchone()
    if inviter_row is None:
        return None  # inviter hasn't graduated yet; leave at 'graduated'

    inviter_uuid = inviter_row["uuid"]
    if _credit_one(tx, inviter_uuid, referral_id):
        tx.execute(_Q_FLIP_GRADUATED_TO_CREDITED, dict(id=referral_id))
    return inviter_uuid


_Q_PENDING_FOR_INVITER = """
    SELECT id FROM referral
     WHERE inviter_email = %(inviter_email)s
       AND status = 'graduated'
"""


def credit_pending_for_inviter(
    tx, inviter_email: str, inviter_person_uuid: str
) -> int:
    """Called at the inviter's own POST /finish-onboarding (typically at
    launch sign-in). Drains all referrals where the invitees already
    graduated but the inviter wasn't yet a person. Returns the count
    actually credited."""
    norm = _normalize_email(inviter_email)
    rows = tx.execute(
        _Q_PENDING_FOR_INVITER, dict(inviter_email=norm)
    ).fetchall()
    n = 0
    for r in rows:
        referral_id = str(r["id"])
        if _credit_one(tx, inviter_person_uuid, referral_id):
            tx.execute(_Q_FLIP_GRADUATED_TO_CREDITED, dict(id=referral_id))
            n += 1
    return n


_Q_MY_STATS = """
    WITH me AS (
        SELECT email, uuid FROM person WHERE uuid = %(uuid)s
    ),
    my_code AS (
        SELECT referral_code FROM beta_signup
         WHERE email = (SELECT email FROM me)
    ),
    my_refs AS (
        SELECT
            count(*)                                          AS joined_count,
            count(*) FILTER (WHERE status = 'credited')       AS credited_count
          FROM referral
         WHERE inviter_email = (SELECT email FROM me)
    ),
    my_pending_value AS (
        SELECT count(*) * 5 AS pending_token_balance
          FROM referral
         WHERE inviter_email = (SELECT email FROM me)
           AND status IN ('pending', 'graduated')
    )
    SELECT
        (SELECT referral_code FROM my_code)               AS code,
        (SELECT joined_count FROM my_refs)                AS joined_count,
        (SELECT credited_count FROM my_refs)              AS credited_count,
        (SELECT pending_token_balance FROM my_pending_value) AS pending_token_balance
"""


def get_my_stats(tx, person_uuid: str) -> dict:
    """For GET /referrals/me. Returns
        {code, joined_count, credited_count, pending_token_balance}
    code may be None if the caller isn't in beta_signup."""
    row = tx.execute(_Q_MY_STATS, dict(uuid=person_uuid)).fetchone()
    return {
        "code": (row or {}).get("code"),
        "joined_count": int((row or {}).get("joined_count") or 0),
        "credited_count": int((row or {}).get("credited_count") or 0),
        "pending_token_balance": int((row or {}).get("pending_token_balance") or 0),
    }
