"""One-off: blast the COMMUNITY referral email to beta_signup rows that
don't have a referral link yet.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_referral_community                       # DRY RUN
    python -m emails.send_referral_community --only a@b.com         # one address
    python -m emails.send_referral_community --all                 # blast
    python -m emails.send_referral_community --all --exclude x@y.z  # hold one out

Dry-run is the default so an accidental invocation never sends mail. The
audience is exactly "hasn't received a referral link yet" (beta_signup with
referral_code IS NULL); a code is minted for each before sending. `--all`
skips suppressed + unsubscribed addresses and anything passed via --exclude.
Excluded addresses are NOT minted a code (their link stays absent)."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.referral_community import send_referral_community, SUBJECT, FROM_ADDR
from service.referrals import mint_code


_Q_TARGETS = """
    SELECT email
      FROM beta_signup
     WHERE unsubscribed_at IS NULL
       AND referral_intro_sent_at IS NULL
     ORDER BY created_at
"""

_Q_MARK_SENT = """
    UPDATE beta_signup
       SET referral_intro_sent_at = NOW()
     WHERE email = %(email)s
"""


def _backfill_and_target_codes(excluded: set[str]) -> list[tuple[str, str]]:
    """[(email, code), ...] for every codeless row not in `excluded`, with
    codes minted as needed. One tx for atomicity. Excluded rows are skipped
    entirely — no code minted — so a held-out person stays without a link."""
    out: list[tuple[str, str]] = []
    with api_tx() as tx:
        for r in tx.execute(_Q_TARGETS).fetchall():
            email = r["email"]
            if email.strip().lower() in excluded:
                continue
            code = mint_code(tx, email)
            if code is None:
                continue  # shouldn't happen — the SELECT proves the row exists
            out.append((email, code))
    return out


def _mark_sent(email: str) -> None:
    with api_tx() as tx:
        tx.execute(_Q_MARK_SENT, dict(email=email.strip().lower()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Blast the Ahavah community referral email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to every codeless beta_signup row")
    ap.add_argument("--exclude", metavar="EMAIL", action="append", default=[],
                    help="address(es) to hold out — not minted, not emailed (repeatable)")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        with api_tx() as tx:
            code = mint_code(tx, email)
        if code is None:
            print(f"FAIL: {email} is not in beta_signup; nothing minted, nothing sent.")
            return
        print(f"Sending single test to {email} (code={code}) ...")
        send_referral_community(email, code)
        _mark_sent(email)
        print("done.")
        return

    excluded = {e.strip().lower() for e in args.exclude}
    targets = _backfill_and_target_codes(excluded)

    if not args.all:
        print(f"DRY RUN — {len(targets)} recipient(s):")
        for e, c in targets:
            suppressed = " [SUPPRESSED — skipped on --all]" if is_suppressed_send(e) else ""
            print(f"   - {e:40s} code={c}{suppressed}")
        print("\nRe-run with --only EMAIL to test one, or --all to send to everyone.")
        return

    sent = skipped = 0
    print(f"Sending to {len(targets)} candidate(s)...")
    for e, c in targets:
        if is_suppressed_send(e):
            print(f"   skip (suppressed) {e}")
            skipped += 1
            continue
        send_referral_community(e, c)
        _mark_sent(e)
        print(f"   sent {e} (code={c})")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
