"""One send loop for every campaign email: suppression, footer unsubscribe,
per-member cap, per-run idempotency, send log. Dry run builds every message
and sends none."""
from __future__ import annotations

from typing import Any, Callable

from emails.base import is_suppressed_send, mask_email
from service.campaigns import campaign_unsubscribed, can_send, log_send
from smtp import make_aws_smtp

def run_campaign(tx_factory, campaign: str, campaign_id: str, recipients: list[dict],
                 build: Callable[[dict], tuple[str, str]], *, send: bool,
                 from_addr: str, unsub_scope: str,
                 list_unsubscribe: Callable[[str], str] | None = None,
                 cap_days: int = 7, exempt: bool = False,
                 post_send: Callable[[Any, dict], None] | None = None) -> dict:
    """`unsub_scope` is the campaign module's own UNSUB_SCOPE: the scope its
    footer and List-Unsubscribe header point at. It is required rather than
    defaulted so a new campaign cannot quietly ship without honouring the
    unsubscribe link it prints in its own footer."""
    smtp = make_aws_smtp() if send else None
    sent = skipped_cap = skipped_suppressed = skipped_unsubscribed = 0
    for row in recipients:
        email = row['email']
        if is_suppressed_send(email):
            skipped_suppressed += 1
            continue
        with tx_factory() as tx:
            if campaign_unsubscribed(tx, row['person_id'], unsub_scope):
                skipped_unsubscribed += 1
                continue
            if not can_send(tx, row['person_id'], campaign, campaign_id, cap_days=cap_days, exempt=exempt):
                skipped_cap += 1
                continue
        # Build inside the try even on a dry run: building IS the thing a dry
        # run exercises, so a template that raises must be reported the same
        # way a failed send is rather than escaping to the caller.
        try:
            subject, html = build(row)
            mid = None
            if smtp is not None:
                mid = smtp.send(subject=subject, body=html, to_addr=email, from_addr=from_addr,
                                list_unsubscribe=list_unsubscribe(email) if list_unsubscribe else None)
        except Exception as e:
            return dict(sent=sent, skipped_cap=skipped_cap, skipped_suppressed=skipped_suppressed,
                        skipped_unsubscribed=skipped_unsubscribed,
                        dry_run=not send, campaign_id=campaign_id,
                        error=str(e), failed_email=mask_email(email))
        if send:
            with tx_factory() as tx:
                log_send(tx, row['person_id'], campaign, campaign_id, str(mid) if mid else None)
                if post_send is not None:
                    post_send(tx, row)
            print(f"sent {campaign} to {mask_email(email)}")
        else:
            print(f"DRY RUN {campaign} {mask_email(email)}")
        sent += 1
    return dict(sent=sent, skipped_cap=skipped_cap, skipped_suppressed=skipped_suppressed,
                skipped_unsubscribed=skipped_unsubscribed,
                dry_run=not send, campaign_id=campaign_id, error=None, failed_email=None)
