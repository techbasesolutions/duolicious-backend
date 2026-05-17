"""
One-time backfill: every existing subscriber gets their tier's monthly
token stipend credited as if a renewal cycle just ran. Idempotent —
re-runnable safely; rows already tagged with backfill_tag='v1-2026-05-16'
are skipped.

Plan deviation: the plan snippet used asyncpg (`async def main`,
`await pool.acquire()`). The ahavah-api codebase is synchronous psycopg
via `database.api_tx()`. This script translates to sync.

Plan deviation 2: the plan reads `person.subscription_tier_key` to pick
the stipend amount, but no such column exists on `person`. We resolve
tier_key via Stripe instead: list each customer's active subscriptions
and read tier_key off the subscription metadata (which is set by
`post_checkout_web` in service/checkout/__init__.py via the
subscription_data.metadata field). Subscribers without an accessible
Stripe customer_id or whose active subscription is missing tier_key are
SKIPPED with a printed warning rather than guessed.

Usage:
  cd ahavah-api
  python -m scripts.backfill_subscription_stipend --dry-run
  # Review output, then:
  python -m scripts.backfill_subscription_stipend

Environment:
  STRIPE_SECRET_KEY      — required. Reads-only API calls.
  DUO_DB_*               — required (same as the API).
"""

from __future__ import annotations

import os
import sys


TIER_TO_STIPEND = {"month": 10, "quart": 12, "year": 15}
BACKFILL_TAG = "v1-2026-05-16"


def _stripe():
    """Initialize the stripe SDK from STRIPE_SECRET_KEY. Exits if unset."""
    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        print("ERROR: STRIPE_SECRET_KEY is not set", file=sys.stderr)
        sys.exit(2)
    try:
        import stripe
    except ImportError:
        print("ERROR: stripe package is not installed", file=sys.stderr)
        sys.exit(2)
    stripe.api_key = api_key
    return stripe


def _tier_key_for_customer(stripe_mod, customer_id: str):
    """Return ('tier_key', sub_id) for the customer's first active
    subscription whose metadata carries tier_key, else (None, None).
    """
    if not customer_id:
        return None, None
    try:
        subs = stripe_mod.Subscription.list(
            customer=customer_id,
            status='active',
            limit=10,
        )
    except Exception as e:
        print(f"  WARN  Stripe Subscription.list failed for {customer_id}: {e}")
        return None, None
    for sub in subs.auto_paging_iter() if hasattr(subs, 'auto_paging_iter') else subs.get('data', []):
        sub_dict = sub.to_dict_recursive() if hasattr(sub, 'to_dict_recursive') else dict(sub)
        md = sub_dict.get('metadata') or {}
        tier_key = md.get('tier_key')
        if tier_key in TIER_TO_STIPEND:
            return tier_key, sub_dict.get('id')
    return None, None


def main(dry_run: bool) -> int:
    from database import api_tx
    from service.tokens import credit

    stripe_mod = _stripe()

    with api_tx('read committed') as tx:
        subs = tx.execute(
            """
            SELECT id, uuid::text AS uuid, stripe_customer_id
              FROM person
             WHERE 'premium' = ANY(entitlements)
               AND stripe_customer_id IS NOT NULL
            """
        ).fetchall()

    print(f"Found {len(subs)} premium subscribers with a Stripe customer id")
    if dry_run:
        print("DRY-RUN — no ledger writes will be made")

    credited = 0
    skipped = 0
    for row in subs:
        person_uuid = row['uuid']
        customer_id = row['stripe_customer_id']

        with api_tx('read committed') as tx:
            existing = tx.execute(
                """
                SELECT 1 FROM token_ledger
                 WHERE person_id = %(pid)s
                   AND reason = 'subscription_stipend'
                   AND metadata->>'backfill_tag' = %(tag)s
                 LIMIT 1
                """,
                dict(pid=person_uuid, tag=BACKFILL_TAG),
            ).fetchone()
        if existing:
            print(f"SKIP {person_uuid} — already backfilled (tag={BACKFILL_TAG})")
            skipped += 1
            continue

        tier_key, sub_id = _tier_key_for_customer(stripe_mod, customer_id)
        if not tier_key:
            print(
                f"SKIP {person_uuid} — no active Stripe subscription with "
                f"tier_key metadata (customer={customer_id})"
            )
            skipped += 1
            continue

        amount = TIER_TO_STIPEND[tier_key]
        if dry_run:
            print(f"DRY  {person_uuid} +{amount} ({tier_key}) sub={sub_id}")
            continue

        with api_tx() as tx:
            credit(
                tx, person_uuid, amount,
                reason='subscription_stipend',
                metadata={
                    'tier_key':              tier_key,
                    'backfill_tag':          BACKFILL_TAG,
                    'stripe_customer_id':    customer_id,
                    'stripe_subscription_id': sub_id,
                },
            )
        print(f"OK   {person_uuid} +{amount} ({tier_key}) sub={sub_id}")
        credited += 1

    print(f"\nDone. credited={credited} skipped={skipped} total={len(subs)}")
    return 0


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    sys.exit(main(dry))
