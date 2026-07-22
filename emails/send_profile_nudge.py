"""One-off: personalised profile-completion nudge.

Computes each member's ACTUAL gaps from the database and emails only
those with at least one. Members whose profile is complete are never
contacted. Dry-run is the default; `--send` is required.

    python -m emails.send_profile_nudge                 # DRY RUN (shows gaps)
    python -m emails.send_profile_nudge --only a@b.com  # send one
    python -m emails.send_profile_nudge --send          # send for real
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.profile_nudge import profile_nudge_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp


# Gaps are read from the SAME places the app reads them, so the email
# can never claim something is missing that the app considers filled.
_Q_TARGETS = """
    SELECT
        email,
        name,
        NOT COALESCE((ahavah_extra->>'citySet')::boolean, FALSE) AS needs_city,
        NOT (ahavah_extra ? 'intent')                            AS needs_intent,
        NOT (ahavah_extra ? 'wantsChildren')                     AS needs_children
    FROM person
    WHERE activated
    ORDER BY email
"""


def gaps_for(row) -> list[str]:
    out = []
    if row["needs_city"]:
        out.append("city")
    if row["needs_intent"]:
        out.append("intent")
    if row["needs_children"]:
        out.append("children")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--only", metavar="EMAIL", help="send to this one address only (test)")
    args = ap.parse_args()

    with api_tx() as tx:
        rows = tx.execute(_Q_TARGETS).fetchall()

    targets = [(r, gaps_for(r)) for r in rows]
    targets = [(r, g) for r, g in targets if g]
    if args.only:
        targets = [(r, g) for r, g in targets if r["email"] == args.only]
        if not targets:
            print(f"{args.only} has no gaps, nothing to send")
            return

    smtp = make_aws_smtp()
    sent = skipped = 0
    for row, gaps in targets:
        email = row["email"]
        if is_suppressed_send(email):
            skipped += 1
            continue
        if not (args.send or args.only):
            print(f"DRY RUN {mask_email(email):28s} gaps: {', '.join(gaps)}")
            sent += 1
            continue
        unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
        smtp.send(
            subject=SUBJECT,
            body=profile_nudge_html(email, gaps),
            to_addr=email,
            from_addr=FROM_ADDR,
            list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        )
        print(f"sent to {mask_email(email)} ({', '.join(gaps)})")
        sent += 1

    mode = "SENT" if (args.send or args.only) else "DRY RUN"
    print(f"{mode}: {sent} target(s), {skipped} suppressed")


if __name__ == "__main__":
    main()
