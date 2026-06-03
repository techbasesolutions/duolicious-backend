"""One-off: email the beta launch announcement to the beta_signup cohort.

Run inside the api container on the droplet (it needs DB + SMTP env):

    python -m emails.send_beta_launch                 # DRY RUN — lists recipients, sends nothing
    python -m emails.send_beta_launch --only a@b.com  # send to one address (test)
    python -m emails.send_beta_launch --all           # send to every beta_signup row

Dry-run is the default so an accidental invocation never sends mail. Intended
for June 15, after the launch is confirmed live.
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.beta_launch import send_beta_launch, SUBJECT, FROM_ADDR


def recipients() -> list[str]:
    with api_tx() as tx:
        rows = tx.execute(
            "SELECT email FROM beta_signup "
            "WHERE unsubscribed_at IS NULL "
            "ORDER BY created_at"
        ).fetchall()
    return [r["email"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Send the Ahavah beta launch announcement.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to all beta_signup rows")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        print(f"Sending single test to {args.only} ...")
        send_beta_launch(args.only)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    rs = recipients()
    if not args.all:
        print(f"DRY RUN — {len(rs)} beta recipient(s):")
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
        send_beta_launch(e)
        print(f"   sent {e}")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
