"""One-off: announce the Marriage Checklist to the list.

Audience = waitlist signups (not unsubscribed) UNION activated members,
deduplicated by normalized address. Dry-run is the default; `--send` is
required to actually send. Suppressed domains (example.com /
techbaseltd.com) are skipped even on --send.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_marriage_checklist_launch                 # DRY RUN
    python -m emails.send_marriage_checklist_launch --only a@b.com  # send one (test)
    python -m emails.send_marriage_checklist_launch --send          # blast
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.marriage_checklist_launch import launch_checklist_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp


_Q_TARGETS = """
    SELECT email FROM waitlist_signup
     WHERE unsubscribed_at IS NULL
    UNION
    SELECT email FROM person
     WHERE activated
    ORDER BY email
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--only", metavar="EMAIL", help="send to this one address only (test)")
    args = ap.parse_args()

    if args.only:
        targets = [args.only]
    else:
        with api_tx() as tx:
            targets = [r["email"] for r in tx.execute(_Q_TARGETS).fetchall()]

    smtp = make_aws_smtp()
    sent = skipped = 0
    for email in targets:
        if is_suppressed_send(email):
            skipped += 1
            continue
        if not (args.send or args.only):
            print(f"DRY RUN would send to {mask_email(email)}")
            sent += 1
            continue
        unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
        smtp.send(
            subject=SUBJECT,
            body=launch_checklist_html(email),
            to_addr=email,
            from_addr=FROM_ADDR,
            list_unsubscribe=f"<mailto:admin@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        )
        print(f"sent to {mask_email(email)}")
        sent += 1

    mode = "SENT" if (args.send or args.only) else "DRY RUN"
    print(f"{mode}: {sent} target(s), {skipped} suppressed")


if __name__ == "__main__":
    main()
