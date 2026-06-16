"""One-off: blast the LAUNCH email to every waitlist registrant.

Each recipient gets a personal claim link (ahavah.app/claim/<token>) that logs
them straight in and pre-fills their account from their waitlist answers.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_launch                 # DRY RUN (default) -- sends nothing
    python -m emails.send_launch --only a@b.com  # send one (test)
    python -m emails.send_launch --send          # blast to every waitlist registrant

Audience = ALL waitlist_signup rows not unsubscribed. Dry-run is the default;
`--send` is required to actually send. Suppressed domains (example.com /
techbaseltd.com) are skipped even on --send."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.launch import launch_html, SUBJECT, FROM_ADDR
from service.config import WEB_BASE_URL
from service.unsubscribe import make_token, make_url
from smtp import make_aws_smtp


_Q_TARGETS = """
    SELECT email FROM waitlist_signup
     WHERE unsubscribed_at IS NULL
     ORDER BY created_at
"""


def _targets() -> list[str]:
    with api_tx() as tx:
        return [r["email"] for r in tx.execute(_Q_TARGETS).fetchall()]


def _claim_url(email: str) -> str:
    # The claim landing page is /claim/<token> (FE route). make_url() builds
    # the /u/<token> UNSUBSCRIBE path, so it is NOT reusable here -- build the
    # claim URL directly from the signed token.
    return f"{WEB_BASE_URL.rstrip('/')}/claim/{make_token('claim', email)}"


def _send_one(email: str) -> None:
    claim_url = _claim_url(email)
    unsub = make_url("waitlist", email, WEB_BASE_URL)
    make_aws_smtp().send(
        subject=SUBJECT,
        body=launch_html(email, claim_url),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=f"<{unsub}>",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Blast the Ahavah launch email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--send", action="store_true",
                    help="actually send to every waitlist registrant")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        if is_suppressed_send(email):
            print(f"{email} is on the suppression list; not sending.")
            return
        print(f"Sending single test to {email} ...")
        _send_one(email)
        print("done.")
        return

    targets = _targets()

    if not args.send:
        # DRY RUN: render one sample to a file + list recipients, send nothing.
        sample = targets[0] if targets else "sample@ahavah.app"
        claim_url = _claim_url(sample)
        with open("/tmp/launch_sample.html", "w", encoding="utf-8") as f:
            f.write(launch_html(sample, claim_url))
        print(f"DRY RUN -- {len(targets)} recipient(s):")
        for e in targets:
            tag = " [SUPPRESSED -- skipped on --send]" if is_suppressed_send(e) else ""
            print(f"   - {e}{tag}")
        print(f"\nSample HTML for {sample} written to /tmp/launch_sample.html")
        print("Re-run with --only EMAIL to test one address, or --send to blast everyone.")
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
