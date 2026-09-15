"""python -m emails.send_spotlight_announcement [--send] [--campaign-id ID] [--preview you@x]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.base import suppressed_sql_pattern
from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT, FROM_ADDR
from service.campaigns import suppressed_predicate_sql, unsubscribed_predicate_sql
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.spotlight import spotlight_confirm_url
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'notifications'

# The same suppression + scope-unsubscribe filters run_campaign() applies
# per-row (see service/campaigns/runner.py) are baked into these queries too,
# so recipient_count() (the admin index number) and recipients() (what a run
# actually sends) can never drift from what a real send would do.
_Q_RECIPIENTS = f"""
    SELECT p.id AS person_id, p.email, p.name FROM person p
     WHERE p.activated AND p.deletion_requested_at IS NULL AND lower(p.email) <> ALL(%(ex)s)
       AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
       AND NOT ({suppressed_predicate_sql('p.email')})
     ORDER BY p.id
"""

def build_for(row: dict) -> tuple[str, str]:
    # Mints a real single-use nonce (service.spotlight.nonce.issue_nonce)
    # against the recipient's current consent epoch -- including on a dry
    # run (send=False), since run_campaign builds every message either way
    # to exercise the template. A dry run of this campaign therefore still
    # writes one spotlight_token_nonce row per recipient it builds for,
    # even though no email goes out and the nonce is never handed to anyone.
    with api_tx() as tx:
        confirm_url = spotlight_confirm_url(tx, row['email'])
    if confirm_url is None:
        # No activated person matches this email (deactivated, deleted, or
        # a bad row) -- there is nothing to mint a token against. Raising
        # here (rather than building a link-less email) makes run_campaign
        # count this recipient as a failure instead of silently sending, or
        # dry-running, a card with href="None".
        raise ValueError('no_person')
    return SUBJECT, spotlight_announcement_html(
        confirm_url,
        f"{WEB_BASE_URL}/settings/privacy",
        _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))

_Q_RECIPIENT_COUNT = f"""
    SELECT count(*) AS n FROM person p
     WHERE p.activated AND p.deletion_requested_at IS NULL AND lower(p.email) <> ALL(%(ex)s)
       AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
       AND NOT ({suppressed_predicate_sql('p.email')})
"""

def recipients() -> list[dict]:
    with api_tx('read committed') as tx:
        return [dict(r) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]

def recipient_count() -> int:
    """How many rows recipients() would return, without materialising them:
    the admin dashboard only needs the number. Opens its own transaction, so
    never call it while holding one (the api connection lock is not
    reentrant)."""
    with api_tx('read committed') as tx:
        return int(tx.execute(_Q_RECIPIENT_COUNT, dict(ex=_excluded(), sup=suppressed_sql_pattern())).fetchone()['n'])

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
                       unsub_scope=UNSUB_SCOPE,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
