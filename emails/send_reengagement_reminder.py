"""One-off: send the re-engagement REMINDER (second nudge).

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_reengagement_reminder                 # DRY RUN
    python -m emails.send_reengagement_reminder --only a@b.com  # send to one
    python -m emails.send_reengagement_reminder --all           # send to all incomplete

Dry-run is the default so an accidental invocation never sends mail. `--all`
targets only rows with empty answers, same selector as the first nudge.
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.reengagement_reminder import (
    send_reengagement_reminder,
    SUBJECT,
    FROM_ADDR,
)


def incomplete_recipients() -> list[str]:
    with api_tx() as tx:
        rows = tx.execute(
            "SELECT email FROM waitlist_signup "
            "WHERE answers IS NULL OR answers = '{}'::jsonb "
            "ORDER BY created_at"
        ).fetchall()
    return [r["email"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Send the Ahavah re-engagement reminder.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to all incomplete waitlist rows")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        print(f"Sending single test to {args.only} ...")
        send_reengagement_reminder(args.only)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    rs = incomplete_recipients()
    if not args.all:
        print(f"DRY RUN - {len(rs)} incomplete waitlist recipient(s):")
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
        send_reengagement_reminder(e)
        print(f"   sent {e}")
        sent += 1
    print(f"done - {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
