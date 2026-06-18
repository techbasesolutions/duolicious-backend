"""One-off: send the COMMUNITY-INVITE email (Facebook group) to everyone who
ever entered an email — waitlist + beta signups + accounts, deduplicated.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_community_invite                       # DRY RUN (default)
    python -m emails.send_community_invite --only a@b.com         # send one (test)
    python -m emails.send_community_invite --send                # blast everyone
    python -m emails.send_community_invite --send --exclude a@b.com  # blast, skipping some

Audience = distinct lower(email) across waitlist_signup + beta_signup + person,
excluding unsubscribed rows. Suppressed domains (example.com / techbaseltd.com)
are skipped even on --send. Dry-run is the default; --send is required to send."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.community_invite import community_invite_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_url
from smtp import make_aws_smtp


_Q_TARGETS = """
    SELECT DISTINCT lower(email) AS email FROM (
        SELECT email, unsubscribed_at FROM waitlist_signup
        UNION ALL SELECT email, unsubscribed_at FROM beta_signup
        UNION ALL SELECT email, NULL AS unsubscribed_at FROM person
    ) u
    WHERE unsubscribed_at IS NULL
    ORDER BY email
"""


def _targets() -> list[str]:
    with api_tx() as tx:
        return [r["email"] for r in tx.execute(_Q_TARGETS).fetchall()]


def _send_one(email: str) -> None:
    unsub = make_url("waitlist", email, WEB_BASE_URL)
    make_aws_smtp().send(
        subject=SUBJECT,
        body=community_invite_html(email),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=f"<{unsub}>",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Send the Ahavah community-invite email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--send", action="store_true", help="actually send to everyone")
    ap.add_argument("--exclude", metavar="EMAIL", action="append", default=[],
                    help="address(es) to skip (e.g. one already test-sent)")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        if is_suppressed_send(email):
            print(f"{email} is suppressed; not sending.")
            return
        print(f"Sending single test to {email} ...")
        _send_one(email)
        print("done.")
        return

    exclude = {e.strip().lower() for e in args.exclude}
    targets = [e for e in _targets() if e not in exclude]

    if not args.send:
        print(f"DRY RUN -- {len(targets)} recipient(s):")
        for e in targets:
            tag = " [SUPPRESSED -- skipped on --send]" if is_suppressed_send(e) else ""
            print(f"   - {e}{tag}")
        print("\nRe-run with --only EMAIL to test, or --send to blast.")
        return

    sent = skipped = 0
    print(f"Sending to {len(targets)} recipient(s)...")
    for e in targets:
        if is_suppressed_send(e):
            print(f"   skip (suppressed) {e}")
            skipped += 1
            continue
        _send_one(e)
        print(f"   sent {e}")
        sent += 1
    print(f"done -- {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
