"""One send loop for every campaign email: suppression, per-member cap,
per-run idempotency, send log. Dry run builds every message and sends none."""
from __future__ import annotations

from typing import Any, Callable

from emails.base import is_suppressed_send, mask_email
from service.campaigns import can_send, log_send
from smtp import make_aws_smtp

def run_campaign(tx_factory, campaign: str, campaign_id: str, recipients: list[dict],
                 build: Callable[[dict], tuple[str, str]], *, send: bool,
                 from_addr: str, list_unsubscribe: Callable[[str], str] | None = None,
                 cap_days: int = 7, exempt: bool = False,
                 post_send: Callable[[Any, dict], None] | None = None) -> dict:
    smtp = make_aws_smtp() if send else None
    sent = skipped_cap = skipped_suppressed = 0
    for row in recipients:
        email = row['email']
        if is_suppressed_send(email):
            skipped_suppressed += 1
            continue
        with tx_factory() as tx:
            if not can_send(tx, row['person_id'], campaign, campaign_id, cap_days=cap_days, exempt=exempt):
                skipped_cap += 1
                continue
        if send:
            try:
                subject, html = build(row)
                mid = smtp.send(subject=subject, body=html, to_addr=email, from_addr=from_addr,
                                list_unsubscribe=list_unsubscribe(email) if list_unsubscribe else None)
            except Exception as e:
                return dict(sent=sent, skipped_cap=skipped_cap, skipped_suppressed=skipped_suppressed,
                            dry_run=not send, campaign_id=campaign_id,
                            error=str(e), failed_email=mask_email(email))
            with tx_factory() as tx:
                log_send(tx, row['person_id'], campaign, campaign_id, str(mid) if mid else None)
                if post_send is not None:
                    post_send(tx, row)
            print(f"sent {campaign} to {mask_email(email)}")
        else:
            subject, html = build(row)
            print(f"DRY RUN {campaign} {mask_email(email)}")
        sent += 1
    return dict(sent=sent, skipped_cap=skipped_cap, skipped_suppressed=skipped_suppressed,
                dry_run=not send, campaign_id=campaign_id, error=None, failed_email=None)
