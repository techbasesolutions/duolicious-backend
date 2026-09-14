"""python -m emails.send_community_weekly [--send] [--campaign-id ID]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.base import suppressed_sql_pattern
from emails.community_weekly import community_weekly_html, SUBJECT, FROM_ADDR
from service.campaigns import make_campaign_link, suppressed_predicate_sql, unsubscribed_predicate_sql
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'community'

# The weekly email runs weekly, so a 7-day cap would race its own cadence:
# a run that starts a few hours "late" would find every member inside the
# window and skip the whole list. 6 days leaves the slack.
CAP_DAYS = 6

# Same suppression + scope-unsubscribe filters run_campaign() applies
# per-row (service/campaigns/runner.py), baked in here too so
# recipient_count() (the admin index number) never overstates what a real
# run would send.
_Q_RECIPIENTS = f"""
    SELECT p.id AS person_id, p.email, p.name FROM person p
     WHERE p.activated AND p.deletion_requested_at IS NULL
       AND lower(p.email) <> ALL(%(ex)s)
       AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
       AND NOT ({suppressed_predicate_sql('p.email')})
     ORDER BY p.id
"""
_Q_NEW = """
    SELECT split_part(name, ' ', 1) AS first_name, country FROM person
     WHERE activated AND sign_up_time > NOW() - interval '7 days' AND lower(email) <> ALL(%(ex)s)
     ORDER BY sign_up_time DESC
"""
_Q_TOTAL = "SELECT count(*) AS n FROM person WHERE activated AND lower(email) <> ALL(%(ex)s)"
_Q_RECIPIENT_COUNT = f"""
    SELECT count(*) AS n FROM person p
     WHERE p.activated AND p.deletion_requested_at IS NULL
       AND lower(p.email) <> ALL(%(ex)s)
       AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
       AND NOT ({suppressed_predicate_sql('p.email')})
"""
# The most recently published member_of_week facebook row, within the last 7
# days. Facebook only (not instagram): the two platform rows share the same
# subject and caption, and the curated post the email links out to is the
# facebook one (post_url below).
#
# The three extra conditions are consent and correctness, not tidiness. A row
# stays 'published' forever, so without them the email would keep re-featuring
# a member for a week after they opted out, or after a removal task was filed
# to take the post down; and `post_url` is built from external_post_id, so a
# published row that never recorded one would link to facebook.com/None.
_Q_SPOTLIGHT = """
    SELECT split_part(p.name, ' ', 1) AS first_name,
           date_part('year', age(p.date_of_birth))::int AS age,
           p.country,
           q.image_url,
           q.external_post_id
      FROM publishing_queue q
      JOIN person p ON p.id = q.subject_person_id
     WHERE q.kind = 'member_of_week' AND q.platform = 'facebook' AND q.status = 'published'
       AND q.updated_at > NOW() - interval '7 days'
       AND p.spotlight_opt_in
       AND p.activated AND p.deletion_requested_at IS NULL
       AND q.external_post_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM spotlight_removal_task t WHERE t.queue_id = q.id)
     ORDER BY q.updated_at DESC
     LIMIT 1
"""

def _spotlight_from_row(row) -> dict:
    return dict(first_name=row['first_name'], age=row['age'], country=row['country'],
                image_url=row['image_url'],
                post_url=f"https://www.facebook.com/{row['external_post_id']}")

def _week_context() -> dict:
    with api_tx('read committed') as tx:
        spotlight_row = tx.execute(_Q_SPOTLIGHT).fetchone()
        return dict(new_members=[dict(r) for r in tx.execute(_Q_NEW, dict(ex=_excluded())).fetchall()],
                    total=int(tx.execute(_Q_TOTAL, dict(ex=_excluded())).fetchone()['n']),
                    spotlight=_spotlight_from_row(spotlight_row) if spotlight_row else None)

def recipients() -> list[dict]:
    """Each row carries the week context this run renders. It is computed
    once per call and travelled on the row rather than parked in a module
    global: two runs (or a run and a preview) must never see each other's
    numbers, and a process that lives for weeks must never serve a stale
    week."""
    week = _week_context()
    with api_tx('read committed') as tx:
        return [dict(r, week=week) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]

def recipient_count() -> int:
    """How many rows recipients() would return, without materialising them or
    computing the week context. Opens its own transaction, so never call it
    while holding one (the api connection lock is not reentrant)."""
    with api_tx('read committed') as tx:
        return int(tx.execute(_Q_RECIPIENT_COUNT, dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchone()['n'])

def build_for(row: dict) -> tuple[str, str]:
    week = row['week']
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e2', f"{WEB_BASE_URL}/discover", row['person_id'] or None)
    return SUBJECT, community_weekly_html(week['new_members'], week['total'], week['spotlight'], cta,
                                          _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))

def preview_row(to: str) -> dict:
    return dict(person_id=0, email=to, name='Preview', week=_week_context())

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e2-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e2', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       unsub_scope=UNSUB_SCOPE, cap_days=CAP_DAYS,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
