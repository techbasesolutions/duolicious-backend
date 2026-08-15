"""Beta-tester cohort. Caller owns the api_tx.

Records onboarders who opt in on the completion screen. email is the natural
key (lower-cased + trimmed). A repeat opt-in while still subscribed is a
no-op, but a repeat opt-in AFTER an unsubscribe must clear unsubscribed_at
(F21) - re-ticking the checkbox is explicit consent and a sticky unsubscribe
that silently eats it is a trap. The confirmation email is fired by the
route only when register() reports either a brand-new row or a resubscribe.
"""

from __future__ import annotations


def _norm(email: str) -> str:
    return (email or "").strip().lower()


_Q_REGISTER = """
  INSERT INTO beta_signup (email, person_id)
  VALUES (%(email)s, %(person_id)s)
  ON CONFLICT (email) DO UPDATE SET
      -- An explicit re-registration IS consent; a sticky unsubscribe
      -- that silently eats the re-opt-in is a trap (F21). The WHERE
      -- guard means the UPDATE (and therefore RETURNING) only fires
      -- when the existing row was actually unsubscribed, so a no-op
      -- repeat opt-in (already subscribed, never unsubscribed) returns
      -- no row at all. `xmax = 0` distinguishes a brand-new INSERT
      -- (created) from an UPDATE that cleared a stale unsubscribe
      -- (resubscribed) - verified against Postgres semantics directly:
      -- fresh insert -> xmax=0/row returned; repeat opt-in while still
      -- subscribed -> WHERE false -> no row; re-opt-in after unsubscribe
      -- -> row returned, xmax<>0, unsubscribed_at cleared.
      unsubscribed_at = NULL
  WHERE beta_signup.unsubscribed_at IS NOT NULL
  RETURNING email, (xmax = 0) AS created
"""

_Q_COUNT = "SELECT count(*) AS n FROM beta_signup"
_Q_EXISTS = "SELECT 1 FROM beta_signup WHERE email = %(email)s"


def register(tx, email: str, person_id) -> tuple[bool, bool]:
    """Upsert the cohort row. Returns (created, resubscribed):
      - (True, False)  - brand-new signup
      - (False, True)  - was unsubscribed, re-opt-in cleared it just now
      - (False, False) - already subscribed, no-op (no row touched)
    Callers should fire the confirmation email whenever created OR
    resubscribed is True, and only then - never on an unchanged existing
    subscription."""
    row = tx.execute(_Q_REGISTER, dict(email=_norm(email), person_id=person_id)).fetchone()
    if row is None:
        return (False, False)
    created = bool(row["created"])
    return (created, not created)


def count(tx) -> int:
    return tx.execute(_Q_COUNT).fetchone()["n"]


def is_beta(tx, email: str) -> bool:
    """True if this email has opted into the beta cohort."""
    return tx.execute(_Q_EXISTS, dict(email=_norm(email))).fetchone() is not None
