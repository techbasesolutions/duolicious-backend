"""One-off: announce the July feature drop to the list.

Audience = waitlist signups (not unsubscribed) UNION activated members,
deduplicated. Dry-run is the default; `--send` is required to actually
send. Suppressed domains are skipped even on --send.

Run inside the api container on the droplet:

    python -m emails.send_new_features                 # DRY RUN
    python -m emails.send_new_features --only a@b.com  # send one
    python -m emails.send_new_features --send          # blast
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.new_features import new_features_html, SUBJECT, FROM_ADDR
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
            body=new_features_html(email),
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
