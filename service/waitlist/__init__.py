"""Pre-signup waitlist capture. Caller owns the api_tx.

A waitlist row is keyed by normalized email (lower-cased + trimmed). answers
is an onboarding-shaped JSONB blob; re-submitting the same email overwrites it
(the landing page posts {email} first, then the full form upserts the rest).
"""

from __future__ import annotations

import json


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


_Q_UPSERT = """
  INSERT INTO waitlist_signup (email, answers)
  VALUES (%(email)s, %(answers)s::jsonb)
  ON CONFLICT (email) DO UPDATE
    SET answers = EXCLUDED.answers, updated_at = NOW()
"""

_Q_GET = """
  SELECT email, answers, created_at, updated_at
    FROM waitlist_signup WHERE email = %(email)s
"""


def upsert(tx, email: str, answers: dict) -> None:
    tx.execute(_Q_UPSERT, dict(
        email=normalize_email(email),
        answers=json.dumps(answers or {}),
    ))


def get(tx, email: str) -> dict | None:
    return tx.execute(_Q_GET, dict(email=normalize_email(email))).fetchone()


_Q_COUNT = "SELECT count(*) AS n FROM waitlist_signup"


def count(tx) -> int:
    return tx.execute(_Q_COUNT).fetchone()["n"]
