"""python -m emails.send_reinvite [--send] [--campaign-id ID]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT, FROM_ADDR
from service.campaigns import make_campaign_link
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import dormant_cohort, newcomers_since, count_newcomers_since
from service.unsubscribe import make_url as _unsub_url

def recipients() -> list[dict]:
    out = []
    with api_tx('read committed') as tx:
        for row in dormant_cohort(tx, days=30, resend_days=30):
            names = newcomers_since(tx, row['person_id'], row['last_action'], limit=5)
            if not names:
                continue
            total = count_newcomers_since(tx, row['person_id'], row['last_action'])
            out.append(dict(**row, newcomers=names, total_new=int(total)))
    return out

def build_for(row: dict) -> tuple[str, str]:
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e3', f"{WEB_BASE_URL}/discover", row['person_id'] or None)
    first = (row.get('name') or 'there').split(' ')[0]
    return SUBJECT, reinvite_html(first, row.get('newcomers', []), row.get('total_new', 0), cta,
                                  _unsub_url('notifications', row['email'], WEB_BASE_URL))

def preview_row(to: str) -> dict:
    return dict(person_id=0, email=to, name='Preview', newcomers=[dict(first_name='Rivka', country='GB')], total_new=1)

def post_send(tx, row: dict) -> None:
    tx.execute("UPDATE person SET reinvite_sent_at = NOW() WHERE id = %(id)s", dict(id=row['person_id']))

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e3-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e3', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url('notifications', e, WEB_BASE_URL)}>",
                       post_send=post_send))

if __name__ == '__main__':
    main()
