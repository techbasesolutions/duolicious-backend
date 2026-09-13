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

def _week_context() -> dict:
    with api_tx('read committed') as tx:
        return dict(new_members=[dict(r) for r in tx.execute(_Q_NEW, dict(ex=_excluded())).fetchall()],
                    total=int(tx.execute(_Q_TOTAL, dict(ex=_excluded())).fetchone()['n']),
                    spotlight=None)   # Phase B fills this from the published queue

_CTX: dict = {}

def recipients() -> list[dict]:
    _CTX.clear()
    _CTX.update(_week_context())
    with api_tx('read committed') as tx:
        return [dict(r) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded())).fetchall()]

def build_for(row: dict) -> tuple[str, str]:
    if not _CTX:
        _CTX.update(_week_context())
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e2', f"{WEB_BASE_URL}/discover", row['person_id'] or None)
    return SUBJECT, community_weekly_html(_CTX['new_members'], _CTX['total'], _CTX['spotlight'], cta,
                                          _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))

def preview_row(to: str) -> dict:
    return dict(person_id=0, email=to, name='Preview')

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e2-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e2', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
