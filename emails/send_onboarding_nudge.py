"""One-off: nudge members who signed up recently but never finished
onboarding (NOT activated, no deletion request, joined in the last
OFFLINE-window days).

    python -m emails.send_onboarding_nudge                  # DRY RUN
    python -m emails.send_onboarding_nudge --only a@b.com   # send one
    python -m emails.send_onboarding_nudge --send           # send for real
    python -m emails.send_onboarding_nudge --preview a@b.com
        # send a copy addressed to the FIRST target's content to this
        # address (for review), without contacting any target
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.onboarding_nudge import (
    onboarding_nudge_html,
    SUBJECT,
    FROM_ADDR,
)
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp

SIGNUP_WINDOW_DAYS = 14

_Q_TARGETS = """
    SELECT p.email, p.name
    FROM person p
    WHERE NOT p.activated
      AND p.deletion_requested_at IS NULL
      AND p.sign_up_time > NOW() - make_interval(days => %(window_days)s)
      -- stalled, not mid-session: last touch over 6 hours ago
      AND p.last_online_time < NOW() - INTERVAL '6 hours'
    ORDER BY p.sign_up_time DESC
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--only", metavar="EMAIL", help="send to this one target only")
    ap.add_argument("--preview", metavar="EMAIL",
                    help="send the first target's email to this address for review")
    args = ap.parse_args()

    with api_tx() as tx:
        rows = tx.execute(
            _Q_TARGETS, dict(window_days=SIGNUP_WINDOW_DAYS)
        ).fetchall()

    if args.only:
        rows = [r for r in rows if r["email"] == args.only]
        if not rows:
            print(f"{args.only} is not a stalled signup, nothing to send")
            return

    smtp = make_aws_smtp()

    if args.preview:
        if not rows:
            print("no targets to preview")
            return
        row = rows[0]
        unsub = _unsub_url("notifications", args.preview, WEB_BASE_URL)
        smtp.send(
            subject=f"[PREVIEW] {SUBJECT}",
            body=onboarding_nudge_html(row["name"], unsub),
            to_addr=args.preview,
            from_addr=FROM_ADDR,
        )
        print(f"preview (content for {mask_email(row['email'])}) sent to {args.preview}")
        return

    sent = skipped = 0
    for row in rows:
        email = row["email"]
        if is_suppressed_send(email):
            skipped += 1
            continue
        if not (args.send or args.only):
            print(f"DRY RUN {mask_email(email):28s} name: {row['name']}")
            sent += 1
            continue
        unsub = _unsub_url("notifications", email, WEB_BASE_URL)
        smtp.send(
            subject=SUBJECT,
            body=onboarding_nudge_html(row["name"], unsub),
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
