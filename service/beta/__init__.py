"""Beta-tester cohort. Caller owns the api_tx.

Records onboarders who opt in on the completion screen. email is the natural
key (lower-cased + trimmed) so a repeat opt-in is a no-op. The confirmation
email is fired by the route only when register() reports a brand-new row.
"""

from __future__ import annotations


def _norm(email: str) -> str:
    return (email or "").strip().lower()


_Q_REGISTER = """
  INSERT INTO beta_signup (email, person_id)
  VALUES (%(email)s, %(person_id)s)
  ON CONFLICT (email) DO NOTHING
  RETURNING email
"""

_Q_COUNT = "SELECT count(*) AS n FROM beta_signup"


def register(tx, email: str, person_id) -> bool:
    """Insert the cohort row. Returns True iff this was a brand-new signup
    (so the caller fires the confirmation email exactly once per email)."""
    row = tx.execute(_Q_REGISTER, dict(email=_norm(email), person_id=person_id)).fetchone()
    return row is not None


def count(tx) -> int:
    return tx.execute(_Q_COUNT).fetchone()["n"]
