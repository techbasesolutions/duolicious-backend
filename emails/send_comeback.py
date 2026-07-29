"""One-off: on-brand come-back invitation to dormancy-deactivated members.

Targets: deactivated accounts (activated = FALSE) that are NOT
pending deletion. Deactivation here only ever means dormancy; members
who asked to delete are in pending-deletion and are never contacted.

    python -m emails.send_comeback                  # DRY RUN
    python -m emails.send_comeback --only a@b.com   # send one
    python -m emails.send_comeback --send           # send for real
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.comeback import comeback_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp

_Q_TARGETS = """
    SELECT p.email, p.name
    FROM person p
    WHERE NOT p.activated
      -- dormancy only: members who asked to delete (mig 0008 soft-delete
      -- grace window) are never contacted
      AND p.deletion_requested_at IS NULL
    ORDER BY p.email
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--only", metavar="EMAIL", help="send to this one address only")
    args = ap.parse_args()

    with api_tx() as tx:
        rows = tx.execute(_Q_TARGETS).fetchall()

    if args.only:
        rows = [r for r in rows if r["email"] == args.only]
        if not rows:
            print(f"{args.only} is not a dormant member, nothing to send")
            return

    smtp = make_aws_smtp()
    sent = skipped = 0
    for row in rows:
        email = row["email"]
        if is_suppressed_send(email):
            skipped += 1
            continue
        if not (args.send or args.only):
            print(f"DRY RUN {mask_email(email)}")
            sent += 1
            continue
        unsub = _unsub_url("notifications", email, WEB_BASE_URL)
        smtp.send(
            subject=SUBJECT,
            body=comeback_html(unsub),
            to_addr=email,
            from_addr=FROM_ADDR,
            list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        )
        print(f"sent to {mask_email(email)}")
        sent += 1

    mode = "SENT" if (args.send or args.only) else "DRY RUN"
    print(f"{mode}: {sent} target(s), {skipped} suppressed")


if __name__ == "__main__":
    main()
