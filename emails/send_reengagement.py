"""One-off: send the re-engagement / demographic-capture email.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_reengagement                 # DRY RUN — lists incomplete waitlist rows
    python -m emails.send_reengagement --only a@b.com  # send to one address (test)
    python -m emails.send_reengagement --all           # send to every INCOMPLETE waitlist row

Dry-run is the default so an accidental invocation never sends mail. `--all`
targets only rows with empty answers (signups who never completed the wizard);
completed signups are not nagged.
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.reengagement import send_reengagement, SUBJECT, FROM_ADDR


def incomplete_recipients() -> list[str]:
    with api_tx() as tx:
        rows = tx.execute(
            "SELECT email FROM waitlist_signup "
            "WHERE (answers IS NULL OR answers = '{}'::jsonb) "
            "  AND unsubscribed_at IS NULL "
            "ORDER BY created_at"
        ).fetchall()
    return [r["email"] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Send the Ahavah re-engagement email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to all incomplete waitlist rows")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        print(f"Sending single test to {args.only} ...")
        send_reengagement(args.only)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    rs = incomplete_recipients()
    if not args.all:
        print(f"DRY RUN — {len(rs)} incomplete waitlist recipient(s):")
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
        send_reengagement(e)
        print(f"   sent {e}")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
