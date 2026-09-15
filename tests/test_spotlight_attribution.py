import secrets
import uuid

import psycopg
import pytest
from pydantic import ValidationError

from database import _api_conninfo, api_tx
from service.campaigns import make_campaign_link, record_click
from service.config import WEB_BASE_URL
from service.spotlight.attribution import attribute_signup
from service.growth.queries import post_stats


# Wave 3b, task 3 rewrote this test. It used to assert that a bare campaign
# key credited the LATEST unmatched human click, which is the F10 defect
# itself: the key is shared by everyone who saw the post, so "latest" picked
# whoever happened to click last. Credit now follows the receipt.

def test_attribute_stamps_person_and_credits_that_receipts_own_click(make_person):
    joiner = make_person(name='Joiner')
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        url = make_campaign_link(tx, f'post:{rk}', f'{WEB_BASE_URL}/discover', None)
        key = url.rsplit('/', 1)[1]
        record_click(tx, key, 'facebookexternalhit/1.1')        # bot, no receipt
        _, receipt = record_click(tx, key, 'Mozilla/5.0 (iPhone)')  # human
        assert attribute_signup(tx, joiner['id'], receipt) is True
        row = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=joiner['id'])).fetchone()
        assert row['spotlight_ref'] == key
        stamped = tx.execute("SELECT ua_class FROM campaign_click WHERE link_key = %(k)s AND signup_person_id = %(pid)s", dict(k=key, pid=joiner['id'])).fetchall()
        assert [r['ua_class'] for r in stamped] == ['mobile']
        assert post_stats(tx, rk) == {'clicks': 1, 'signups': 1}
        assert attribute_signup(tx, joiner['id'], 'unknownkey') is False
        assert attribute_signup(tx, joiner['id'], None) is False


# Rides the legacy campaign-key branch on purpose (removed in task 8); the
# first-touch rule itself is re-proved on receipts in
# test_first_touch_wins_and_does_not_consume_the_second_receipt below.
def test_attribute_does_not_overwrite(make_person):
    p = make_person(name='Twice')
    rk_a = uuid.uuid4().hex
    rk_b = uuid.uuid4().hex
    with api_tx() as tx:
        a = make_campaign_link(tx, f'post:{rk_a}', f'{WEB_BASE_URL}/discover', None).rsplit('/', 1)[1]
        b = make_campaign_link(tx, f'post:{rk_b}', f'{WEB_BASE_URL}/discover', None).rsplit('/', 1)[1]
        record_click(tx, a, 'Mozilla/5.0'); record_click(tx, b, 'Mozilla/5.0')
        assert attribute_signup(tx, p['id'], a) is True
        assert attribute_signup(tx, p['id'], b) is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['spotlight_ref'] == a


def test_attribute_unknown_key_is_a_no_op(make_person):
    p = make_person(name='Unknown')
    with api_tx() as tx:
        assert attribute_signup(tx, p['id'], 'nosuchkey') is False
        row = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()
        assert row['spotlight_ref'] is None


def test_post_finish_onboarding_model_validates_spotlight_ref():
    from duotypes import PostFinishOnboarding

    assert PostFinishOnboarding().spotlight_ref is None
    assert PostFinishOnboarding(spotlight_ref='abc123').spotlight_ref == 'abc123'
    with pytest.raises(ValidationError):
        PostFinishOnboarding(spotlight_ref='x' * 40)


# ---------------------------------------------------------------------------
# Wave 3b, task 2: record_click mints a per-click receipt (F10). The shared
# campaign_link.key cannot prove a particular visitor clicked; the receipt
# minted here is what a later task will make the only thing that earns
# credit.
# ---------------------------------------------------------------------------

def test_record_click_mints_a_receipt(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        target, receipt = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    assert target and receipt and len(receipt) <= 32
    with api_tx() as tx:
        row = tx.execute("SELECT receipt, ua_class FROM campaign_click WHERE receipt = %(r)s", dict(r=receipt)).fetchone()
    assert row['ua_class'] == 'mobile'

def test_a_bot_click_is_recorded_but_carries_no_receipt(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        target, receipt = record_click(tx, key, 'facebookexternalhit/1.1')
    assert target and receipt is None
    with api_tx() as tx:
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s AND receipt IS NULL", dict(k=key)).fetchone()['n']
    assert n == 1

def test_two_clicks_mint_different_receipts(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, a = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        _, b = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    assert a != b

def test_platform_is_stored_when_named(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)', platform='instagram')
    with api_tx() as tx:
        assert tx.execute("SELECT platform FROM campaign_click WHERE receipt = %(r)s", dict(r=r)).fetchone()['platform'] == 'instagram'

def test_an_unknown_platform_is_stored_as_null(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)', platform='myspace')
    with api_tx() as tx:
        assert tx.execute("SELECT platform FROM campaign_click WHERE receipt = %(r)s", dict(r=r)).fetchone()['platform'] is None

def test_receipt_length_is_within_the_wire_limit():
    # duotypes.PostFinishOnboarding.spotlight_ref and
    # service.spotlight.attribution.MAX_REF_LEN both cap at 32 characters --
    # assert the actual token length rather than trusting the library.
    import secrets
    assert len(secrets.token_urlsafe(24)) == 32


# ---------------------------------------------------------------------------
# Wave 3b, task 3: only a valid, unconsumed receipt earns credit (F10).
# One test per acceptance criterion the adversarial review named: no click,
# expired, single use, another visitor's receipt, a bot's receipt, first
# touch, and the legacy campaign key.
#
# `make_person` returns the whole person row, so every test below takes
# `['id']` from it; the brief's sketch wrote `pid = make_person()`.
# ---------------------------------------------------------------------------

def test_no_click_gets_no_credit(make_campaign_link, make_person):
    make_campaign_link()   # minted, never clicked
    pid = make_person()['id']
    with api_tx() as tx:
        # A receipt-shaped value that was never minted earns nothing.
        assert attribute_signup(tx, pid, 'not-a-real-receipt-value') is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=pid)).fetchone()['spotlight_ref'] is None


def test_an_expired_receipt_gets_no_credit(make_campaign_link, make_person):
    key = make_campaign_link(); pid = make_person()['id']
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        tx.execute("UPDATE campaign_click SET clicked_at = NOW() - interval '8 days' WHERE receipt = %(r)s", dict(r=r))
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r) is False


def test_a_receipt_is_single_use(make_campaign_link, make_person):
    key = make_campaign_link(); a, b = make_person()['id'], make_person()['id']
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, a, r) is True
    with api_tx() as tx:
        assert attribute_signup(tx, b, r) is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=b)).fetchone()['spotlight_ref'] is None


def test_one_visitors_receipt_cannot_be_claimed_by_another(make_campaign_link, make_person):
    # Two visitors click the same link. Each receipt credits only its own click row.
    key = make_campaign_link(); a, b = make_person()['id'], make_person()['id']
    with api_tx() as tx:
        _, ra = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        _, rb = record_click(tx, key, 'Mozilla/5.0 (Android)')
    with api_tx() as tx:
        assert attribute_signup(tx, a, ra) is True
    with api_tx() as tx:
        assert attribute_signup(tx, b, rb) is True
    with api_tx() as tx:
        rows = tx.execute("SELECT receipt, signup_person_id FROM campaign_click WHERE link_key = %(k)s ORDER BY id", dict(k=key)).fetchall()
    assert {r['receipt']: r['signup_person_id'] for r in rows} == {ra: a, rb: b}


def test_a_bot_receipt_does_not_exist_to_be_claimed(make_campaign_link, make_person):
    key = make_campaign_link(); pid = make_person()['id']
    with api_tx() as tx:
        _, r = record_click(tx, key, 'facebookexternalhit/1.1')
    assert r is None
    with api_tx() as tx:
        assert attribute_signup(tx, pid, 'anything') is False


def test_first_touch_wins_and_does_not_consume_the_second_receipt(make_campaign_link, make_person):
    k1, k2 = make_campaign_link(), make_campaign_link(); pid = make_person()['id']
    with api_tx() as tx:
        _, r1 = record_click(tx, k1, 'Mozilla/5.0 (iPhone)')
        _, r2 = record_click(tx, k2, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r1) is True
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r2) is False
        row = tx.execute("SELECT signup_person_id, consumed_at FROM campaign_click WHERE receipt = %(r)s", dict(r=r2)).fetchone()
    assert row['signup_person_id'] is None and row['consumed_at'] is None


def test_the_legacy_campaign_key_still_stamps_but_credits_nothing(make_campaign_link, make_person):
    # Compatibility window only: an old web build sends the shared key.
    key = make_campaign_link(); pid = make_person()['id']
    with api_tx() as tx:
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, pid, key) is True
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=pid)).fetchone()['spotlight_ref'] == key
        credited = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s AND signup_person_id IS NOT NULL", dict(k=key)).fetchone()['n']
    assert credited == 0


# ---------------------------------------------------------------------------
# Wave 3b, task 3, fix round 1. Two clauses the first cut asserted in prose
# but never actually exercised.
# ---------------------------------------------------------------------------

def test_a_bot_click_that_somehow_carries_a_receipt_is_still_refused(make_campaign_link, make_person):
    # The `ua_class <> 'bot'` clause in the consuming UPDATE was untestable
    # through normal minting, because record_click gives a bot no receipt at
    # all, so `receipt = %(ref)s` never reached it and the clause would have
    # survived deletion. Force the case the clause exists for: a bot row
    # that does carry a receipt must still earn nothing.
    key = make_campaign_link(); pid = make_person()['id']
    with api_tx() as tx:
        _, minted = record_click(tx, key, 'facebookexternalhit/1.1')
    assert minted is None

    forced = secrets.token_urlsafe(24)
    with api_tx() as tx:
        tx.execute(
            "UPDATE campaign_click SET receipt = %(r)s WHERE link_key = %(k)s AND ua_class = 'bot'",
            dict(r=forced, k=key))

    with api_tx() as tx:
        assert attribute_signup(tx, pid, forced) is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=pid)).fetchone()['spotlight_ref'] is None
        row = tx.execute("SELECT signup_person_id, consumed_at FROM campaign_click WHERE receipt = %(r)s", dict(r=forced)).fetchone()
    assert row['signup_person_id'] is None and row['consumed_at'] is None


def test_losing_a_concurrent_claim_declines_instead_of_failing_the_signup(make_campaign_link, make_person):
    # api_tx shares one global connection behind a lock, so two api_tx
    # blocks can never overlap. A genuinely concurrent claim therefore needs
    # a second connection of its own, opened on the same conninfo so it
    # inherits the same REPEATABLE READ default.
    key = make_campaign_link()
    winner, loser = make_person()['id'], make_person()['id']
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)')

    rival = psycopg.Connection.connect(conninfo=_api_conninfo, row_factory=psycopg.rows.dict_row)
    try:
        with api_tx() as tx:
            # Take our REPEATABLE READ snapshot BEFORE the rival commits.
            tx.execute('SELECT 1')

            # The rival claims the one receipt and commits.
            rival_cur = rival.cursor()
            assert attribute_signup(rival_cur, winner, r) is True
            rival.commit()

            # We lose the race. Under REPEATABLE READ this is a
            # SerializationFailure inside the consuming UPDATE, not a
            # zero-row match. It must be answered with False, not an
            # exception.
            assert attribute_signup(tx, loser, r) is False

            # And, the whole point: our transaction must still be alive.
            # Without the savepoint the connection would be in
            # InFailedSqlTransaction here and this would raise, taking the
            # entire finish-onboarding request down with it.
            assert tx.execute("SELECT 42 AS n").fetchone()['n'] == 42
            tx.execute(
                "UPDATE person SET about = %(a)s WHERE id = %(p)s",
                dict(a='still writable after a lost race', p=loser))
        # Leaving the block committed cleanly. If it had not, this test
        # would have raised rather than reached here.
    finally:
        rival.close()

    # Exactly one claimant, and the loser's sign-up survived unattributed.
    with api_tx() as tx:
        click = tx.execute("SELECT signup_person_id FROM campaign_click WHERE receipt = %(r)s", dict(r=r)).fetchone()
        w = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=winner)).fetchone()
        l = tx.execute("SELECT spotlight_ref, about FROM person WHERE id = %(p)s", dict(p=loser)).fetchone()
    assert click['signup_person_id'] == winner
    assert w['spotlight_ref'] == key
    assert l['spotlight_ref'] is None
    assert l['about'] == 'still writable after a lost race'
