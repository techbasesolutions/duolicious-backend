"""Premium + referral emails, with the one-time backfill.

Waves:
  welcome  - organic members (no beta_signup row, no completed waitlist
             row): the people the old founding gate skipped. They get
             the grant via --backfill and the "you now have Premium"
             email.
  reminder - everyone else on Premium (the beta/waitlist cohort).

Order of operations for the 2026-08-09 rollout:
    python -m emails.send_premium_referral --backfill        # grant + mint codes
    python -m emails.send_premium_referral                   # DRY RUN both waves
    python -m emails.send_premium_referral --preview a@b.com # both variants to one inbox
    python -m emails.send_premium_referral --send            # send both waves

--backfill is idempotent: the grant is a full no-op for anyone already
on Premium and code minting never re-mints. Emails always read the
member's LIVE subscription_expires_at and referral_code, so the copy
cannot drift from the database.
"""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send, mask_email
from emails.premium_referral import (
    welcome_html,
    reminder_html,
    SUBJECT_WELCOME,
    SUBJECT_REMINDER,
    FROM_ADDR,
)
from service.config import WEB_BASE_URL
from service.entitlements import grant_founding_member_if_eligible
from service.referrals import mint_person_code
from service.unsubscribe import make_url as _unsub_url
from smtp import make_aws_smtp

# Welcome wave = members whose Premium arrived in THIS rollout's
# backfill, evidenced by a fresh starter-stipend ledger row. A
# beta/waitlist-membership predicate is the wrong signal here: several
# post-launch members already carried Premium from their own
# onboarding (loose email matching between person and the signup
# tables), and telling them "your account has been upgraded" would be
# false. The ledger row is written iff the grant actually fired.
_SQL_JUST_GRANTED = """
    EXISTS (
        SELECT 1 FROM token_ledger tl
         WHERE tl.person_id = p.uuid
           AND tl.reason = 'subscription_stipend'
           AND tl.created_at > NOW() - INTERVAL '2 days'
    )
"""

_Q_MEMBERS = f"""
    SELECT p.id, p.uuid::TEXT AS uuid, p.email, p.name,
           p.referral_code, p.subscription_expires_at,
           ({_SQL_JUST_GRANTED}) AS is_organic
      FROM person p
     WHERE p.activated AND p.email <> 'admin@ahavah.app'
     ORDER BY p.sign_up_time
"""


def _load_members():
    with api_tx() as tx:
        return tx.execute(_Q_MEMBERS).fetchall()


def do_backfill() -> None:
    members = _load_members()
    granted = minted = 0
    for m in members:
        if m["referral_code"] is None:
            with api_tx() as tx:
                mint_person_code(tx, m["id"])
            minted += 1
        if grant_founding_member_if_eligible(m["id"], m["uuid"], m["email"]):
            granted += 1
            print(f"granted premium + stipend: {mask_email(m['email'])}")
    print(f"backfill: {granted} granted, {minted} codes minted, "
          f"{len(members)} members total")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="grant premium to organic members + mint codes for all")
    ap.add_argument("--send", action="store_true",
                    help="actually send (default: dry run)")
    ap.add_argument("--preview", metavar="EMAIL",
                    help="send BOTH rendered variants to this address only")
    args = ap.parse_args()

    if args.backfill:
        do_backfill()
        return

    members = _load_members()
    smtp = make_aws_smtp()

    if args.preview:
        # Realistic fixtures: first organic member for welcome, first
        # founding member for reminder, so the preview shows REAL dates
        # and REAL codes.
        organic = next((m for m in members if m["is_organic"]), None)
        founding = next((m for m in members if not m["is_organic"]), None)
        unsub = _unsub_url("notifications", args.preview, WEB_BASE_URL)
        for label, subject, builder, fixture in (
            ("welcome", SUBJECT_WELCOME, welcome_html, organic),
            ("reminder", SUBJECT_REMINDER, reminder_html, founding),
        ):
            if fixture is None or fixture["subscription_expires_at"] is None \
                    or fixture["referral_code"] is None:
                print(f"preview {label}: no fully-backfilled fixture member; "
                      f"run --backfill first")
                continue
            smtp.send(
                subject=f"[PREVIEW {label}] {subject}",
                body=builder(
                    fixture["subscription_expires_at"],
                    f"https://ahavah.app/i/{fixture['referral_code']}",
                    unsub,
                ),
                to_addr=args.preview,
                from_addr=FROM_ADDR,
            )
            print(f"preview {label} sent to {args.preview} "
                  f"(fixture {mask_email(fixture['email'])})")
        return

    sent = skipped = 0
    for m in members:
        if is_suppressed_send(m["email"]):
            skipped += 1
            continue
        if m["subscription_expires_at"] is None or m["referral_code"] is None:
            print(f"SKIP not backfilled: {mask_email(m['email'])}")
            skipped += 1
            continue
        wave = "welcome" if m["is_organic"] else "reminder"
        if not args.send:
            print(f"DRY RUN [{wave}] {mask_email(m['email'])} "
                  f"premium_until={m['subscription_expires_at']:%Y-%m-%d} "
                  f"code={m['referral_code']}")
            sent += 1
            continue
        unsub = _unsub_url("notifications", m["email"], WEB_BASE_URL)
        builder = welcome_html if wave == "welcome" else reminder_html
        subject = SUBJECT_WELCOME if wave == "welcome" else SUBJECT_REMINDER
        smtp.send(
            subject=subject,
            body=builder(
                m["subscription_expires_at"],
                f"https://ahavah.app/i/{m['referral_code']}",
                unsub,
            ),
            to_addr=m["email"],
            from_addr=FROM_ADDR,
            list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        )
        print(f"sent [{wave}] to {mask_email(m['email'])}")
        sent += 1

    mode = "SENT" if args.send else "DRY RUN"
    print(f"{mode}: {sent} recipient(s), {skipped} skipped/suppressed")


if __name__ == "__main__":
    main()
