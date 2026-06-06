"""One-off: blast the referral-intro email to the beta cohort.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_referral_intro                 # DRY RUN — lists recipients + codes
    python -m emails.send_referral_intro --only a@b.com  # send to one address (test)
    python -m emails.send_referral_intro --all           # blast every beta_signup row

Dry-run is the default so an accidental invocation never sends mail.
`--all` skips: suppressed addresses (example.com / techbaseltd.com),
unsubscribed addresses, and addresses that already received this
email (referral_intro_sent_at IS NOT NULL). Re-running is idempotent.

The backfill phase runs FIRST on every invocation (including --only)
so we never email someone whose row lacks a code yet."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.referral_intro import send_referral_intro, SUBJECT, FROM_ADDR
from service.referrals import mint_code


_Q_TARGETS = """
    SELECT bs.email
      FROM beta_signup bs
      JOIN waitlist_signup ws ON ws.email = bs.email
     WHERE bs.unsubscribed_at IS NULL
       AND bs.referral_intro_sent_at IS NULL
       AND jsonb_typeof(ws.answers) = 'object'
       AND ws.answers <> '{}'::jsonb
     ORDER BY bs.created_at
"""

_Q_GET_CODE = """
    SELECT referral_code FROM beta_signup WHERE email = %(email)s
"""

_Q_MARK_SENT = """
    UPDATE beta_signup
       SET referral_intro_sent_at = NOW()
     WHERE email = %(email)s
"""


def _backfill_and_target_codes() -> list[tuple[str, str]]:
    """Returns [(email, code), ...] for every row that still needs an
    email blast, with codes minted as needed. Run inside a single tx
    for atomicity — partial failure leaves rows un-coded but un-emailed
    too, which is the correct invariant."""
    out: list[tuple[str, str]] = []
    with api_tx() as tx:
        targets = tx.execute(_Q_TARGETS).fetchall()
        for r in targets:
            email = r["email"]
            code = mint_code(tx, email)
            if code is None:
                continue  # shouldn't happen — the SELECT proves the row exists
            out.append((email, code))
    return out


def _get_code(email: str) -> str | None:
    with api_tx() as tx:
        row = tx.execute(_Q_GET_CODE, dict(email=email.strip().lower())).fetchone()
        return row["referral_code"] if row else None


def _mark_sent(email: str) -> None:
    with api_tx() as tx:
        tx.execute(_Q_MARK_SENT, dict(email=email.strip().lower()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Blast the Ahavah referral-intro email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to every beta_signup row that hasn't been emailed yet")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        # Backfill the single row's code if needed.
        with api_tx() as tx:
            code = mint_code(tx, email)
        if code is None:
            print(f"FAIL: {email} is not in beta_signup; nothing minted, nothing sent.")
            return
        print(f"Sending single test to {email} (code={code}) ...")
        send_referral_intro(email, code)
        _mark_sent(email)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    targets = _backfill_and_target_codes()
    if not args.all:
        print(f"DRY RUN — {len(targets)} recipient(s) (suppressed addresses included for visibility):")
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
        send_referral_intro(e, c)
        _mark_sent(e)
        print(f"   sent {e} (code={c})")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
