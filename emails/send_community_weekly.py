"""python -m emails.send_community_weekly [--send] [--campaign-id ID]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.community_weekly import community_weekly_html, SUBJECT, FROM_ADDR
from service.campaigns import make_campaign_link
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'community'

# The weekly email runs weekly, so a 7-day cap would race its own cadence:
# a run that starts a few hours "late" would find every member inside the
# window and skip the whole list. 6 days leaves the slack.
CAP_DAYS = 6

_Q_RECIPIENTS = """
    SELECT id AS person_id, email, name FROM person
     WHERE activated AND deletion_requested_at IS NULL AND community_unsubscribed_at IS NULL
       AND lower(email) <> ALL(%(ex)s) ORDER BY id
"""
_Q_NEW = """
    SELECT split_part(name, ' ', 1) AS first_name, country FROM person
     WHERE activated AND sign_up_time > NOW() - interval '7 days' AND lower(email) <> ALL(%(ex)s)
     ORDER BY sign_up_time DESC
"""
_Q_TOTAL = "SELECT count(*) AS n FROM person WHERE activated AND lower(email) <> ALL(%(ex)s)"
_Q_RECIPIENT_COUNT = """
    SELECT count(*) AS n FROM person
     WHERE activated AND deletion_requested_at IS NULL AND community_unsubscribed_at IS NULL
       AND lower(email) <> ALL(%(ex)s)
"""

def _week_context() -> dict:
    with api_tx('read committed') as tx:
        return dict(new_members=[dict(r) for r in tx.execute(_Q_NEW, dict(ex=_excluded())).fetchall()],
                    total=int(tx.execute(_Q_TOTAL, dict(ex=_excluded())).fetchone()['n']),
                    spotlight=None)   # Phase B fills this from the published queue

def recipients() -> list[dict]:
    """Each row carries the week context this run renders. It is computed
    once per call and travelled on the row rather than parked in a module
    global: two runs (or a run and a preview) must never see each other's
    numbers, and a process that lives for weeks must never serve a stale
    week."""
    week = _week_context()
    with api_tx('read committed') as tx:
        return [dict(r, week=week) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded())).fetchall()]

def recipient_count() -> int:
    """How many rows recipients() would return, without materialising them or
    computing the week context. Opens its own transaction, so never call it
    while holding one (the api connection lock is not reentrant)."""
    with api_tx('read committed') as tx:
        return int(tx.execute(_Q_RECIPIENT_COUNT, dict(ex=_excluded())).fetchone()['n'])

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
