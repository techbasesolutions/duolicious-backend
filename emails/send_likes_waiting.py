"""One-off: tell members with unanswered likes how many are waiting.

Targets: activated members with at least MIN_LIKES unanswered likes
(liker not yet liked back, no block either way) who have been offline
for at least OFFLINE_DAYS days. Never emails anyone active enough to
have seen the app recently, and never tells likers anything.

    python -m emails.send_likes_waiting                  # DRY RUN
    python -m emails.send_likes_waiting --only a@b.com   # send one
    python -m emails.send_likes_waiting --send           # send for real
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.likes_waiting import (
    likes_waiting_html,
    subject_for,
    FROM_ADDR,
)
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp

MIN_LIKES = 1
OFFLINE_DAYS = 3

_Q_TARGETS = """
    SELECT
        p.email,
        p.name,
        count(*) AS likes_waiting
    FROM person p
    JOIN liked l ON l.liked_id = p.id
    WHERE p.activated
      AND p.last_online_time < NOW() - make_interval(days => %(offline_days)s)
      -- unanswered: they haven't liked this liker back
      AND NOT EXISTS (
          SELECT 1 FROM liked lb
          WHERE lb.liker_id = p.id AND lb.liked_id = l.liker_id
      )
      -- and there is no block either way
      AND NOT is_blocked_pair(p.id, l.liker_id)
      -- the liker still has an account worth answering
      AND EXISTS (
          SELECT 1 FROM person lp WHERE lp.id = l.liker_id AND lp.activated
      )
    GROUP BY p.email, p.name
    HAVING count(*) >= %(min_likes)s
    ORDER BY count(*) DESC
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--only", metavar="EMAIL", help="send to this one address only")
    args = ap.parse_args()

    with api_tx() as tx:
        rows = tx.execute(
            _Q_TARGETS,
            dict(offline_days=OFFLINE_DAYS, min_likes=MIN_LIKES),
        ).fetchall()

    if args.only:
        rows = [r for r in rows if r["email"] == args.only]
        if not rows:
            print(f"{args.only} is not in the target list, nothing to send")
            return

    smtp = make_aws_smtp()
    sent = skipped = 0
    for row in rows:
        email = row["email"]
        if is_suppressed_send(email):
            skipped += 1
            continue
        if not (args.send or args.only):
            print(f"DRY RUN {mask_email(email):28s} likes waiting: {row['likes_waiting']}")
            sent += 1
            continue
        unsub = _unsub_url("notifications", email, WEB_BASE_URL)
        smtp.send(
            subject=subject_for(row["likes_waiting"]),
            body=likes_waiting_html(row["likes_waiting"], unsub),
            to_addr=email,
            from_addr=FROM_ADDR,
            list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        )
        print(f"sent to {mask_email(email)} ({row['likes_waiting']} likes)")
        sent += 1

    mode = "SENT" if (args.send or args.only) else "DRY RUN"
    print(f"{mode}: {sent} target(s), {skipped} suppressed")


if __name__ == "__main__":
    main()
