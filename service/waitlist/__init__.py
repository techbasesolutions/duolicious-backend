"""Pre-signup waitlist capture. Caller owns the api_tx.

A waitlist row is keyed by normalized email (lower-cased + trimmed). answers
is an onboarding-shaped JSONB blob; re-submitting the same email overwrites it
(the landing page posts {email} first, then the full form upserts the rest).
"""

from __future__ import annotations

import json


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# Single atomic statement that locks the existing row (FOR UPDATE) before
# the UPSERT so two concurrent posts cannot both observe "no prior answers"
# and both report `became_complete=true` (audit Data Integrity #4 — TOCTOU).
# The CTE captures the pre-upsert answers; the upsert then runs against the
# same locked row; the final SELECT compares them to compute became_complete.
_Q_UPSERT = """
  WITH prev AS (
    SELECT answers AS prev_answers
      FROM waitlist_signup
     WHERE email = %(email)s
     FOR UPDATE
  ),
  ins AS (
    INSERT INTO waitlist_signup (email, answers)
    VALUES (%(email)s, %(answers)s::jsonb)
    ON CONFLICT (email) DO UPDATE
      SET answers    = EXCLUDED.answers,
          updated_at = NOW()
    RETURNING (created_at = updated_at) AS inserted
  )
  SELECT ins.inserted,
         (
           %(now_complete)s
           AND NOT COALESCE(
             jsonb_typeof((SELECT prev_answers FROM prev)) = 'object'
             AND (SELECT prev_answers FROM prev) <> '{}'::jsonb,
             FALSE
           )
         ) AS became_complete
    FROM ins
"""

_Q_GET = """
  SELECT email, answers, created_at, updated_at
    FROM waitlist_signup WHERE email = %(email)s
"""


def upsert(tx, email: str, answers: dict) -> dict:
    """Insert or update the waitlist row.

    Returns {"inserted": bool, "became_complete": bool}:
      - inserted: this was a brand-new row (fire the welcome email once).
      - became_complete: the row went from no answers to having answers, i.e.
        the signer-upper just completed the demographic wizard (fire the
        admin "completed onboarding" notice once).

    Computed inside a single SQL statement with `FOR UPDATE` on the pre-upsert
    row so two concurrent posts cannot both report `became_complete=true`.
    """
    norm = normalize_email(email)
    now_complete = bool(answers)
    row = tx.execute(_Q_UPSERT, dict(
        email=norm,
        answers=json.dumps(answers or {}),
        now_complete=now_complete,
    )).fetchone()
    return {
        "inserted": bool(row and row["inserted"]),
        "became_complete": bool(row and row["became_complete"]),
    }


def get(tx, email: str) -> dict | None:
    return tx.execute(_Q_GET, dict(email=normalize_email(email))).fetchone()


_Q_COUNT = "SELECT count(*) AS n FROM waitlist_signup"


def count(tx) -> int:
    return tx.execute(_Q_COUNT).fetchone()["n"]
