"""Catch-up runner for the member welcome email.

The evergreen path (service/person -> send_member_welcome_async) covers
everyone from deploy time onward. This runner exists for members who
onboarded in a window where no welcome fired, e.g. the 2026-08-10 gap
between the premium-wave send and the evergreen wiring.

    python -m emails.send_member_welcome                    # DRY RUN
    python -m emails.send_member_welcome --preview a@b.com  # render to one inbox
    python -m emails.send_member_welcome --send             # send to the window

Default window start: 2026-08-10 14:00 UTC (the premium-wave send;
everyone before it got either the backfill welcome or the reminder).
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.member_welcome import send_member_welcome, member_welcome_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp

DEFAULT_SINCE = "2026-08-10T14:00:00+00:00"

_Q_WINDOW = """
    SELECT email, name, subscription_expires_at, referral_code
      FROM person
     WHERE activated
       AND email <> 'admin@ahavah.app'
       AND sign_up_time > %(since)s
     ORDER BY sign_up_time
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help=f"window start, ISO timestamp (default {DEFAULT_SINCE})")
    ap.add_argument("--send", action="store_true", help="actually send")
    ap.add_argument("--preview", metavar="EMAIL",
                    help="send one rendered copy (first window member's data) here")
    args = ap.parse_args()

    with api_tx() as tx:
        members = tx.execute(_Q_WINDOW, dict(since=args.since)).fetchall()

    if args.preview:
        fixture = next(
            (m for m in members
             if m["subscription_expires_at"] and m["referral_code"]),
            None)
        if fixture is None:
            print("no fully-granted member in the window to preview with")
            return
        unsub = _unsub_url("notifications", args.preview, WEB_BASE_URL)
        make_aws_smtp().send(
            subject=f"[PREVIEW] {SUBJECT}",
            body=member_welcome_html(
                fixture["subscription_expires_at"],
                f"https://ahavah.app/i/{fixture['referral_code']}",
                unsub,
            ),
            to_addr=args.preview,
            from_addr=FROM_ADDR,
        )
        print(f"preview sent to {args.preview} "
              f"(fixture {mask_email(fixture['email'])})")
        return

    sent = skipped = 0
    for m in members:
        if is_suppressed_send(m["email"]):
            skipped += 1
            continue
        if not args.send:
            ok = bool(m["subscription_expires_at"] and m["referral_code"])
            print(f"DRY RUN {mask_email(m['email'])} ({m['name']}) "
                  f"grant_ready={ok}")
            sent += 1
            continue
        if send_member_welcome(m["email"]):
            print(f"sent to {mask_email(m['email'])}")
            sent += 1
        else:
            print(f"SKIP not-ready/suppressed: {mask_email(m['email'])}")
            skipped += 1

    mode = "SENT" if args.send else "DRY RUN"
    print(f"{mode}: {sent}, skipped {skipped}, window since {args.since}")


if __name__ == "__main__":
    main()
