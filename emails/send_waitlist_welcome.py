"""One-off: send the waitlist welcome email to existing registrants.

Run inside the api container on the droplet (it needs DB + SMTP env):

    python -m emails.send_waitlist_welcome                 # DRY RUN — lists recipients, sends nothing
    python -m emails.send_waitlist_welcome --only a@b.com  # send to one address (test)
    python -m emails.send_waitlist_welcome --all           # send to every waitlist row

Dry-run is the default so an accidental invocation never sends mail.
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.waitlist_welcome import send_waitlist_welcome, SUBJECT, FROM_ADDR


def recipients() -> list[str]:
    with api_tx() as tx:
        rows = tx.execute(
            "SELECT email FROM waitlist_signup ORDER BY created_at"
        ).fetchall()
    return [r["email"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Send the Ahavah waitlist welcome email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to all waitlist rows")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        print(f"Sending single test to {args.only} ...")
        send_waitlist_welcome(args.only)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    rs = recipients()
    if not args.all:
        print(f"DRY RUN — {len(rs)} recipient(s) on the waitlist:")
        for e in rs:
            print(f"   - {e}")
        print("\nRe-run with --only EMAIL to test one, or --all to send to everyone.")
        return

    sent = skipped = 0
    print(f"Sending to {len(rs)} recipient(s)...")
    for e in rs:
        if is_suppressed_send(e):
            print(f"   skip (sample) {e}")
            skipped += 1
            continue
        send_waitlist_welcome(e)
        print(f"   sent {e}")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
