"""One enqueue loop for every campaign email: suppression, footer
unsubscribe, per-member cap, per-run idempotency, outbox row. Dry run builds
every message and queues none.

This used to be the send loop as well: it opened an SMTP connection and
talked to it from inside the per-recipient loop. It no longer sends anything.
A run now writes one `email_outbox` row per recipient and returns
immediately; `service.campaigns.outbox.drain` (driven by the `emailoutbox`
cron) does the sending. That is what makes a run survive an api restart, a
transient SMTP failure, and an admin who clicks send twice.

`queued` counts rows this run actually added (a second run with the same
campaign_id queues nothing, which is the idempotency guarantee). `built`
counts messages successfully built, which is the only number a DRY RUN can
report -- a dry run builds everything and queues nothing, so its `queued` is
always 0.
"""
from __future__ import annotations

from typing import Callable

from emails.base import is_suppressed_send, mask_email
from service.campaigns import campaign_unsubscribed, can_send
from service.campaigns import outbox

def run_campaign(tx_factory, campaign: str, campaign_id: str, recipients: list[dict],
                 build: Callable[[dict], tuple[str, str]], *, send: bool,
                 from_addr: str, unsub_scope: str,
                 list_unsubscribe: Callable[[str], str] | None = None,
                 cap_days: int = 7, exempt: bool = False,
                 post_send: str | None = None) -> dict:
    """`unsub_scope` is the campaign module's own UNSUB_SCOPE: the scope its
    footer and List-Unsubscribe header point at. It is required rather than
    defaulted so a new campaign cannot quietly ship without honouring the
    unsubscribe link it prints in its own footer.

    `post_send` is the NAME of an `outbox.POST_SEND_HOOKS` entry, not a
    callable: the effect has to run in the same transaction as the eventual
    ACCEPTANCE, which happens in a different process from this one (see
    service/campaigns/outbox.py)."""
    queued = built = skipped_cap = skipped_suppressed = skipped_unsubscribed = 0
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
        # way a failed enqueue is rather than escaping to the caller.
        try:
            subject, html = build(row)
        except Exception as e:
            return dict(queued=queued, built=built, skipped_cap=skipped_cap,
                        skipped_suppressed=skipped_suppressed,
                        skipped_unsubscribed=skipped_unsubscribed,
                        dry_run=not send, campaign_id=campaign_id,
                        error=str(e), failed_email=mask_email(email))
        built += 1
        if send:
            with tx_factory() as tx:
                queued_id = outbox.enqueue(
                    tx, campaign=campaign, campaign_id=campaign_id, person_id=row['person_id'],
                    email=email, subject=subject, html=html, from_addr=from_addr,
                    unsub_scope=unsub_scope,
                    list_unsubscribe=list_unsubscribe(email) if list_unsubscribe else None,
                    exempt=exempt, cap_days=cap_days, post_send=post_send)
            if queued_id is not None:
                queued += 1
                print(f"queued {campaign} for {mask_email(email)}")
            else:
                # Already in the outbox for this campaign_id: a re-run of the
                # same campaign, which is exactly what enqueue's unique key is
                # there to absorb. Saying "queued" here would make a repeat
                # run's log look like a second send.
                print(f"already queued {campaign} for {mask_email(email)}")
        else:
            print(f"DRY RUN {campaign} {mask_email(email)}")
    return dict(queued=queued, built=built, skipped_cap=skipped_cap,
                skipped_suppressed=skipped_suppressed,
                skipped_unsubscribed=skipped_unsubscribed,
                dry_run=not send, campaign_id=campaign_id, error=None, failed_email=None)
