"""python -m emails.send_spotlight_announcement [--send] [--campaign-id ID] [--preview you@x]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT, FROM_ADDR
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.spotlight import spotlight_confirm_url
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'notifications'

_Q_RECIPIENTS = """
    SELECT id AS person_id, email, name FROM person
     WHERE activated AND deletion_requested_at IS NULL AND lower(email) <> ALL(%(ex)s)
     ORDER BY id
"""

def build_for(row: dict) -> tuple[str, str]:
    return SUBJECT, spotlight_announcement_html(
        spotlight_confirm_url(row['email']),
        f"{WEB_BASE_URL}/settings/privacy",
        _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))

def recipients() -> list[dict]:
    with api_tx('read committed') as tx:
        return [dict(r) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded())).fetchall()]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--campaign-id', default=None)
    ap.add_argument('--preview', default=None)
    a = ap.parse_args()
    if a.preview:
        subject, html = build_for(dict(person_id=0, email=a.preview, name='Preview'))
        from smtp import make_aws_smtp
        make_aws_smtp().send(subject=subject, body=html, to_addr=a.preview, from_addr=FROM_ADDR)
        print(f"preview sent to {a.preview}"); return
    cid = a.campaign_id or f"e1-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e1', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
