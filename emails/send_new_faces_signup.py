"""E6 sender: people who created an account but never finished onboarding.

    python -m emails.send_new_faces_signup                 # dry run
    python -m emails.send_new_faces_signup --send
    python -m emails.send_new_faces_signup --preview you@x

Runs through service.campaigns.runner like E1 to E3, so it inherits the
7-day frequency cap, the suppression list, the unsubscribe scope and the
outbox (at-least-once with visible uncertainty). It is NOT the old
emails.send_onboarding_nudge, which talks straight to SMTP with none of
that and only looks 14 days back.
"""
from __future__ import annotations

import argparse, uuid

from database import api_tx
from emails.base import suppressed_sql_pattern
from emails.new_faces_signup import new_faces_signup_html, SUBJECT, FROM_ADDR
from service.campaigns import (make_campaign_link, suppressed_predicate_sql,
                               unsubscribed_predicate_sql)
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded, count_joiners_of_gender
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'notifications'

# Gender binary by product decision (see the Ahavah gender note): a man is
# shown the count of women who joined, and a woman the count of men. Anyone
# whose gender is not one of those two, or is unset, gets the neutral count.
_OPPOSITE = {1: 2, 2: 1}

# Not activated, not deleting, still reachable. No sign-up window: somebody
# who stalled in June is exactly who this is for.
_Q_RECIPIENTS = f"""
    SELECT p.id AS person_id, p.email, p.name, p.gender_id, p.sign_up_time
      FROM person p
     WHERE NOT p.activated
       AND p.deletion_requested_at IS NULL
       AND p.email IS NOT NULL AND p.email <> ''
       AND lower(p.email) <> ALL(%(ex)s)
       AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
       AND NOT ({suppressed_predicate_sql('p.email')})
     ORDER BY p.sign_up_time DESC
"""

_Q_COUNTRIES = """
    SELECT count(DISTINCT country) AS n FROM person
     WHERE activated AND country IS NOT NULL AND country <> ''
"""

_Q_ANY_JOINERS = """
    SELECT count(*) AS n FROM person p
     WHERE p.activated AND p.id <> %(pid)s AND lower(p.email) <> ALL(%(ex)s)
       AND p.sign_up_time > %(since)s
"""


def recipients() -> list[dict]:
    with api_tx('read committed') as tx:
        rows = [dict(r) for r in tx.execute(
            _Q_RECIPIENTS, dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]
        countries = tx.execute(_Q_COUNTRIES).fetchone()['n']
        for row in rows:
            opposite = _OPPOSITE.get(row['gender_id'])
            if opposite:
                row['joined_count'] = count_joiners_of_gender(
                    tx, row['person_id'], opposite, row['sign_up_time'])
                row['gender_label'] = 'women' if opposite == 2 else 'men'
            else:
                row['joined_count'] = tx.execute(
                    _Q_ANY_JOINERS, dict(pid=row['person_id'], since=row['sign_up_time'],
                                         ex=_excluded())).fetchone()['n']
                row['gender_label'] = 'new members'
            row['country_count'] = int(countries)
    return rows


def recipient_count() -> int:
    with api_tx('read committed') as tx:
        return len(tx.execute(_Q_RECIPIENTS,
                              dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchall())


def build_for(row: dict) -> tuple[str, str]:
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e6', f"{WEB_BASE_URL}/", row['person_id'] or None)
    first = (row.get('name') or '').split(' ')[0] or None
    return SUBJECT, new_faces_signup_html(
        first, int(row.get('joined_count') or 0), row.get('gender_label') or 'new members',
        int(row.get('country_count') or 0), cta,
        _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))


def preview_row(to: str) -> dict:
    return dict(person_id=0, email=to, name='Preview', gender_id=1,
                joined_count=5, gender_label='women', country_count=13)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e6-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e6', cid, recipients(), build_for, send=a.send,
                       from_addr=FROM_ADDR, unsub_scope=UNSUB_SCOPE,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))


if __name__ == '__main__':
    main()
