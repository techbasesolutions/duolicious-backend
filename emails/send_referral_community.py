"""One-off: blast the COMMUNITY referral email to WAITLIST registrants who
completed onboarding (answered the preliminary questions) and have not yet
been sent any referral email.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_referral_community                       # DRY RUN
    python -m emails.send_referral_community --only a@b.com         # one address
    python -m emails.send_referral_community --all                 # blast
    python -m emails.send_referral_community --all --exclude x@y.z  # hold one out

Audience = the WAITLIST track: waitlist_signup rows with non-empty `answers`
(completed the waitlist onboarding), not unsubscribed, who have NOT already
been sent a referral email. Referral codes live on beta_signup, so each
target is enrolled into the referral cohort (a plain INSERT — entitlement-
neutral, since completed-waitlist users already qualify for beta entitlements
via service/entitlements) and then minted a code. `--exclude` holds addresses
out entirely (not enrolled, not minted, not emailed). Dry-run is the default."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.referral_community import send_referral_community, SUBJECT, FROM_ADDR
from service.beta import register
from service.referrals import mint_code


_Q_TARGETS = """
    SELECT ws.email
      FROM waitlist_signup ws
     WHERE jsonb_typeof(ws.answers) = 'object'
       AND ws.answers <> '{}'::jsonb
       AND ws.unsubscribed_at IS NULL
       AND lower(ws.email) NOT IN (
           SELECT lower(email) FROM beta_signup
            WHERE referral_intro_sent_at IS NOT NULL
       )
     ORDER BY ws.created_at
"""

_Q_MARK_SENT = """
    UPDATE beta_signup
       SET referral_intro_sent_at = NOW()
     WHERE email = %(email)s
"""


def _backfill_and_target_codes(excluded: set[str]) -> list[tuple[str, str]]:
    """[(email, code), ...] for every completed-waitlist target not in
    `excluded`. Each target is enrolled into the referral cohort (plain
    INSERT, no side effects) so the code has a home, then minted a code.
    One tx for atomicity. Excluded addresses are skipped entirely."""
    out: list[tuple[str, str]] = []
    with api_tx() as tx:
        for r in tx.execute(_Q_TARGETS).fetchall():
            email = r["email"]
            if email.strip().lower() in excluded:
                continue
            register(tx, email, None)  # enrol in referral cohort (idempotent)
            code = mint_code(tx, email)
            if code is None:
                continue  # uncommitted gate — shouldn't happen for this audience
            out.append((email, code))
    return out


def _mark_sent(email: str) -> None:
    with api_tx() as tx:
        tx.execute(_Q_MARK_SENT, dict(email=email.strip().lower()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Blast the Ahavah community referral email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to every completed-waitlist target")
    ap.add_argument("--exclude", metavar="EMAIL", action="append", default=[],
                    help="address(es) to hold out — not enrolled, not minted, not emailed")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        with api_tx() as tx:
            register(tx, email, None)
            code = mint_code(tx, email)
        if code is None:
            print(f"FAIL: {email} is not eligible (uncommitted); nothing minted, nothing sent.")
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
