"""
F10: entitlements expiry sweep.

expire_stale (service/entitlements/__init__.py) existed with zero callers
before Task 7 wired it into service/cron/entitlements. This test locks its
behavior: strip 'premium' where subscription_expires_at has passed, leave
rows with a future expiry or a NULL expiry untouched.
"""
from datetime import datetime, timezone

from database import api_tx
from service.entitlements import expire_stale


def test_expire_stale_strips_past_expiry_premium(make_person):
    p = make_person(name='Lapsed', gender='Man')
    keep = make_person(name='Current', gender='Woman')
    with api_tx() as tx:
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NOW() - INTERVAL '1 day' "
                   "WHERE id = %(p)s", dict(p=p['id']))
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NOW() + INTERVAL '30 days' "
                   "WHERE id = %(p)s", dict(p=keep['id']))
    n = expire_stale(datetime.now(timezone.utc))
    assert n >= 1
    with api_tx() as tx:
        lapsed = tx.execute("SELECT 'premium' = ANY(entitlements) AS h FROM person "
                            "WHERE id = %(p)s", dict(p=p['id'])).fetchone()['h']
        kept = tx.execute("SELECT 'premium' = ANY(entitlements) AS h FROM person "
                          "WHERE id = %(p)s", dict(p=keep['id'])).fetchone()['h']
    assert not lapsed and kept


def test_expire_stale_skips_null_expiry_even_with_premium(make_person):
    """A member holding 'premium' with a NULL subscription_expires_at must
    NOT be treated as expired. Shouldn't happen in current data (every
    premium grant sets an expiry), but the sweep's WHERE clause must not
    silently strip on NULL < now (NULL comparisons are neither true nor
    false in SQL, so the naive risk is a WHERE that accidentally matches
    or a rewrite that drops the IS NOT NULL guard)."""
    p = make_person(name='NullExpiry', gender='Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET entitlements = ARRAY['premium'], "
                   "subscription_expires_at = NULL "
                   "WHERE id = %(p)s", dict(p=p['id']))
    expire_stale(datetime.now(timezone.utc))
    with api_tx() as tx:
        still_has = tx.execute("SELECT 'premium' = ANY(entitlements) AS h FROM person "
                               "WHERE id = %(p)s", dict(p=p['id'])).fetchone()['h']
    assert still_has
