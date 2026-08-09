"""
Phase 5 Task 5.2 — IAP entitlements: pure DB layer.

This module is the persistence-only side of monetization. The HTTP-facing
RevenueCat webhook lives next door at `service/revenuecat_webhook/`; it
calls into here for grant/revoke/has-entitlement checks.

Data model (see migration 0005):
  person.entitlements              TEXT[]       — currently active entitlements
  person.subscription_expires_at   TIMESTAMPTZ  — top-of-stack expiry (nullable)
  entitlement_event                table        — append-only RevenueCat ledger

Public surface:
  has_entitlement(person_id, name)        → bool
  grant(person_id, name, expires_at)      → True if changed
  revoke(person_id, name)                 → True if changed
  list_entitlements(person_id)            → list[str]
  record_event(event_id, type, app_user_id, payload) → bool   (False = replay)
  expire_stale(now)                       → int   (count of expired strips)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from database import api_tx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def has_entitlement(person_id: int, name: str) -> bool:
    """O(1) gate-check used by `require_entitlement` decorator on premium routes."""
    if not person_id or not name:
        return False
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT %(n)s = ANY(entitlements) AS has FROM person WHERE id = %(id)s',
            dict(id=person_id, n=name),
        ).fetchone()
    return bool(row and row['has'])


def list_entitlements(person_id: int) -> List[str]:
    if not person_id:
        return []
    with api_tx('read committed') as tx:
        row = tx.execute(
            'SELECT entitlements FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
    return list(row['entitlements']) if row else []


# ---------------------------------------------------------------------------
# Write — single-entitlement granularity
# ---------------------------------------------------------------------------

def extend_premium_tx(tx, person_uuid: str, days: int) -> None:
    """Extend a member's Premium by `days`, INSIDE the caller's tx.

    Referral rewards run inside the /finish-onboarding transaction, so
    this cannot use grant() (which opens its own api_tx). Semantics:
    extension is anchored at GREATEST(current expiry, now) — a member
    whose Premium lapsed starts a fresh `days` from now instead of
    getting a back-dated extension; an active member stacks on top of
    their remaining time. Ensures 'premium' is present in entitlements
    (a lapsed member being re-extended gets the flag back)."""
    if not person_uuid or days <= 0:
        return
    tx.execute(
        """
        UPDATE person
           SET subscription_expires_at =
                   GREATEST(COALESCE(subscription_expires_at, NOW()), NOW())
                   + make_interval(days => %(days)s),
               entitlements = CASE
                   WHEN 'premium' = ANY(entitlements) THEN entitlements
                   ELSE array_append(entitlements, 'premium')
               END
         WHERE uuid = uuid_or_null(%(uuid)s)
        """,
        dict(uuid=str(person_uuid), days=days),
    )

def grant(
    person_id: int,
    name: str,
    expires_at: Optional[datetime] = None,
) -> bool:
    """Add `name` to person.entitlements (idempotent — re-granting a
    name already present is a no-op). Optionally updates
    subscription_expires_at to the LATER of (current, new) so that if
    a user subscribes to two SKUs with overlapping expiry, the later
    wins.

    Returns True if person.entitlements changed; False if already present.
    """
    if not person_id or not name:
        return False

    with api_tx() as tx:
        row = tx.execute(
            'SELECT entitlements, subscription_expires_at FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
        if not row:
            return False

        current = list(row['entitlements'] or [])
        already_has = name in current

        # Only update expiry when the new value is later than what's stored.
        new_expires = row['subscription_expires_at']
        if expires_at is not None:
            if new_expires is None or expires_at > new_expires:
                new_expires = expires_at

        if already_has and new_expires == row['subscription_expires_at']:
            return False

        if already_has:
            # Just bump the expiry, don't append.
            tx.execute(
                'UPDATE person SET subscription_expires_at = %(exp)s WHERE id = %(id)s',
                dict(id=person_id, exp=new_expires),
            )
            return False   # entitlements set didn't change

        tx.execute(
            """
            UPDATE person
               SET entitlements             = array_append(entitlements, %(name)s),
                   subscription_expires_at  = %(exp)s
             WHERE id = %(id)s
            """,
            dict(id=person_id, name=name, exp=new_expires),
        )
    return True


def revoke(person_id: int, name: str) -> bool:
    """Strip `name` from person.entitlements. Returns True if removed,
    False if it wasn't present (or person not found)."""
    if not person_id or not name:
        return False

    with api_tx() as tx:
        row = tx.execute(
            'SELECT entitlements FROM person WHERE id = %(id)s',
            dict(id=person_id),
        ).fetchone()
        if not row:
            return False
        if name not in (row['entitlements'] or []):
            return False

        tx.execute(
            """
            UPDATE person
               SET entitlements = array_remove(entitlements, %(name)s)
             WHERE id = %(id)s
            """,
            dict(id=person_id, name=name),
        )
    return True


# ---------------------------------------------------------------------------
# Replay-protected ledger writes
# ---------------------------------------------------------------------------

def record_event(
    event_id: str,
    event_type: str,
    app_user_id: str,
    payload: dict,
) -> bool:
    """Insert into the append-only ledger. Returns True if newly inserted,
    False if the event_id was already present (i.e., this is a replay).

    Callers must:
      1. Call this FIRST.
      2. If it returned False, return 200 to RevenueCat without re-applying
         the entitlement change.
      3. If it returned True, proceed to grant/revoke.

    The ON CONFLICT (event_id) DO NOTHING + RETURNING idiom gives us
    atomic replay-detection without a transaction boundary or a SELECT
    pre-check that could race.
    """
    if not event_id:
        # Without an event_id we can't dedupe — better to drop than to
        # double-apply. Caller logs the malformed event.
        return False

    with api_tx() as tx:
        row = tx.execute(
            """
            INSERT INTO entitlement_event (event_id, event_type, app_user_id, payload)
            VALUES (%(eid)s, %(etype)s, %(uid)s, %(payload)s::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            dict(
                eid=event_id,
                etype=event_type,
                uid=str(app_user_id),
                payload=json.dumps(payload),
            ),
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Reconciliation / cron-driven cleanup
# ---------------------------------------------------------------------------

def expire_stale(now: Optional[datetime] = None) -> int:
    """Strip entitlements whose subscription_expires_at has passed.

    Called by the nightly reconciliation cron (Task 5.2 Step 5). Returns
    the number of person rows that had their entitlements array changed.

    We only strip the LAST entitlement when expiry passes — this assumes a
    user has at most one paid SKU at a time (premium OR boost-pack OR
    nothing). Multi-SKU support would need per-entitlement expiry tracking,
    which Phase 5 doesn't ship. The reconciliation cron also re-checks
    against RevenueCat's REST API, which is the authoritative source for
    renewals — webhook drops can't leave a user permanently demoted.
    """
    now = now or datetime.now(timezone.utc)
    with api_tx() as tx:
        result = tx.execute(
            """
            UPDATE person
               SET entitlements            = '{}',
                   subscription_expires_at = NULL
             WHERE entitlements <> '{}'
               AND subscription_expires_at IS NOT NULL
               AND subscription_expires_at < %(now)s
            """,
            dict(now=now),
        )
        # psycopg's cursor exposes rowcount on UPDATE.
        return result.rowcount if hasattr(result, 'rowcount') else 0


# ---------------------------------------------------------------------------
# Early-member Premium grant
# ---------------------------------------------------------------------------

_FOUNDING_MEMBER_PREMIUM_DAYS = 183  # ~6 months

_FOUNDING_MEMBER_STARTER_TOKENS = 30  # one month's stipend equivalent


def grant_founding_member_if_eligible(
    person_id: int,
    person_uuid: str,
    email: str,
) -> bool:
    """Grant the 6-month Premium early-member perk + a one-time starter
    token stipend so the user can actually USE Premium features
    (super-like, boost, day pass, etc.) the moment they finish
    onboarding instead of staring at "Not enough tokens" toasts.

    Eligibility (2026-08-09, owner decision): EVERY new member. The
    original founding gate (beta_signup / completed waitlist row)
    silently excluded organic signups, who arrived to a paid-feeling
    app while launch-era members rode free. The perk name is kept for
    call-site stability.

    Returns True iff Premium was granted for the first time.
    Idempotent: if the member already carries the 'premium'
    entitlement, this is a FULL no-op — it must NOT bump the expiry,
    because /finish-onboarding replays used to re-extend the window
    by 183 days from each replay (observed: two members with expiries
    ~6 months past their cohort's).

    Opens its own api_tx for the check + stipend write, then delegates
    to grant() which opens another tx. Caller must NOT be holding an
    api_tx when calling this (will deadlock or open a nested tx).
    """
    if not person_id or not email:
        return False

    # Replay guard: an existing 'premium' entitlement means the perk
    # (or a purchase) is already in force. Never re-extend from here.
    if has_entitlement(person_id, 'premium'):
        return False

    expires_at = datetime.now(timezone.utc) + timedelta(days=_FOUNDING_MEMBER_PREMIUM_DAYS)
    granted = grant(person_id, 'premium', expires_at=expires_at)
    if granted and person_uuid:
        # Local import to avoid an entitlements <-> tokens import cycle
        # at module load. The function is small and well-defined.
        from service.tokens import credit
        with api_tx() as tx:
            credit(
                tx, str(person_uuid), _FOUNDING_MEMBER_STARTER_TOKENS,
                reason='subscription_stipend',
                metadata={'source': 'founding_member_starter'},
            )
    return granted
