"""Referral rewards + early-member Premium (2026-08-09 owner decision).

Rules locked in here:
  1. A successful referral rewards the inviter with 5 tokens AND 30
     days of Premium, exactly once per referral (the token-ledger row
     is the idempotency latch for both).
  2. The Premium extension anchors at GREATEST(current expiry, now):
     active members stack on top of remaining time, lapsed members
     restart from now.
  3. EVERY new member gets the 6-month early-member Premium grant +
     30 starter tokens at finish-onboarding, not just beta/waitlist
     "founding" members.
  4. Replaying the grant is a FULL no-op: it must not re-extend the
     expiry (the old behavior stretched two real members ~6 months
     past their cohort).
  5. Every member can mint a personal referral code, and that code
     resolves through the same attribution path as beta-era codes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database import api_tx
from service.entitlements import grant_founding_member_if_eligible
from service.referrals import (
    attribute,
    credit_pending_for_invitee,
    mint_person_code,
    REFERRAL_REWARD_TOKENS,
    REFERRAL_REWARD_PREMIUM_DAYS,
)


def _email_of(tx, person_id: int) -> str:
    return tx.execute(
        'SELECT email FROM person WHERE id = %(id)s', dict(id=person_id)
    ).fetchone()['email']


def _premium_state(tx, person_id: int):
    return tx.execute(
        """
        SELECT 'premium' = ANY(entitlements) AS has_premium,
               subscription_expires_at
          FROM person WHERE id = %(id)s
        """,
        dict(id=person_id),
    ).fetchone()


def _token_balance(tx, person_uuid: str) -> int:
    row = tx.execute(
        """
        -- token_ledger.person_id holds the person UUID (tokens service
        -- convention), not the integer person.id.
        SELECT COALESCE(SUM(delta), 0) AS balance
          FROM token_ledger
         WHERE person_id = uuid_or_null(%(u)s)
        """,
        dict(u=person_uuid),
    ).fetchone()
    return int(row['balance'])


def _about(expected: datetime, actual: datetime, tolerance_s: int = 120) -> bool:
    return abs((expected - actual).total_seconds()) <= tolerance_s


# --- Rule 1 + 2: referral rewards -----------------------------------------

def test_referral_credit_rewards_tokens_and_premium_once(make_person):
    inviter = make_person(name='Inviter', gender='Man')
    invitee = make_person(name='Invitee', gender='Woman')

    with api_tx() as tx:
        inviter_email = _email_of(tx, inviter['id'])
        invitee_email = _email_of(tx, invitee['id'])
        tx.execute(
            """
            INSERT INTO referral (inviter_email, invitee_email)
            VALUES (%(a)s, %(b)s)
            """,
            dict(a=inviter_email, b=invitee_email),
        )
        before_tokens = _token_balance(tx, inviter['uuid'])

        credit_pending_for_invitee(tx, invitee_email)

        state = _premium_state(tx, inviter['id'])
        assert state['has_premium'], 'referral must grant premium flag'
        expected = datetime.now(timezone.utc) + timedelta(
            days=REFERRAL_REWARD_PREMIUM_DAYS)
        assert _about(expected, state['subscription_expires_at']), (
            f"expected ~+{REFERRAL_REWARD_PREMIUM_DAYS}d, "
            f"got {state['subscription_expires_at']}"
        )
        assert (_token_balance(tx, inviter['uuid'])
                == before_tokens + REFERRAL_REWARD_TOKENS)

        status = tx.execute(
            'SELECT status FROM referral WHERE invitee_email = %(e)s',
            dict(e=invitee_email),
        ).fetchone()['status']
        assert status == 'credited'

        # Replay: same invitee finishing onboarding again must not
        # double-reward.
        credit_pending_for_invitee(tx, invitee_email)
        state2 = _premium_state(tx, inviter['id'])
        assert state2['subscription_expires_at'] == state['subscription_expires_at']
        assert (_token_balance(tx, inviter['uuid'])
                == before_tokens + REFERRAL_REWARD_TOKENS)

        # Cleanup (referral has no FK to person fixtures).
        tx.execute('DELETE FROM referral WHERE invitee_email = %(e)s',
                   dict(e=invitee_email))


def test_referral_extension_stacks_on_active_premium(make_person):
    inviter = make_person(name='Stacker', gender='Man')
    invitee = make_person(name='Newbie', gender='Woman')

    with api_tx() as tx:
        inviter_email = _email_of(tx, inviter['id'])
        invitee_email = _email_of(tx, invitee['id'])
        tx.execute(
            """
            UPDATE person
               SET entitlements = ARRAY['premium'],
                   subscription_expires_at = NOW() + INTERVAL '100 days'
             WHERE id = %(id)s
            """,
            dict(id=inviter['id']),
        )
        tx.execute(
            """
            INSERT INTO referral (inviter_email, invitee_email)
            VALUES (%(a)s, %(b)s)
            """,
            dict(a=inviter_email, b=invitee_email),
        )

        credit_pending_for_invitee(tx, invitee_email)

        state = _premium_state(tx, inviter['id'])
        expected = datetime.now(timezone.utc) + timedelta(
            days=100 + REFERRAL_REWARD_PREMIUM_DAYS)
        assert _about(expected, state['subscription_expires_at']), (
            'active premium must stack, not restart'
        )

        tx.execute('DELETE FROM referral WHERE invitee_email = %(e)s',
                   dict(e=invitee_email))


# --- Rule 3 + 4: early-member grant ----------------------------------------

def test_early_member_grant_covers_organic_signups(make_person):
    """fixture emails are random @example.com addresses that exist in
    neither beta_signup nor waitlist_signup, i.e. organic members. The
    old founding gate returned False for them; the widened grant must
    cover them."""
    p = make_person(name='Organic', gender='Woman')

    with api_tx() as tx:
        email = _email_of(tx, p['id'])
        tokens_before = _token_balance(tx, p['uuid'])

    granted = grant_founding_member_if_eligible(p['id'], p['uuid'], email)
    assert granted, 'organic member must receive the early-member grant'

    with api_tx() as tx:
        state = _premium_state(tx, p['id'])
        assert state['has_premium']
        expected = datetime.now(timezone.utc) + timedelta(days=183)
        assert _about(expected, state['subscription_expires_at'])
        assert _token_balance(tx, p['uuid']) == tokens_before + 30
        first_expiry = state['subscription_expires_at']

    # Replay (e.g. /finish-onboarding retry) must be a FULL no-op:
    # False return, expiry byte-identical, no extra tokens.
    granted_again = grant_founding_member_if_eligible(p['id'], p['uuid'], email)
    assert not granted_again

    with api_tx() as tx:
        state2 = _premium_state(tx, p['id'])
        assert state2['subscription_expires_at'] == first_expiry
        assert _token_balance(tx, p['uuid']) == tokens_before + 30


# --- Rule 5: personal codes ------------------------------------------------

def test_mint_person_code_idempotent_and_resolvable(make_person):
    member = make_person(name='Coded', gender='Man')

    with api_tx() as tx:
        code1 = mint_person_code(tx, member['id'])
        code2 = mint_person_code(tx, member['id'])
        assert code1 and code1 == code2, 'minting must be idempotent'
        assert len(code1) == 7

        member_email = _email_of(tx, member['id'])
        row = attribute(tx, code1, 'brand-new-invitee@example.org')
        assert row is not None, 'person code must resolve through attribute()'
        assert row['inviter_email'] == member_email

        # Self-referral through a person code is void.
        assert attribute(tx, code1, member_email) is None

        tx.execute(
            "DELETE FROM referral WHERE invitee_email = 'brand-new-invitee@example.org'"
        )
