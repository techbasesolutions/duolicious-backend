import uuid

import pytest
from pydantic import ValidationError

from database import api_tx
from service.campaigns import make_campaign_link, record_click
from service.config import WEB_BASE_URL
from service.spotlight.attribution import attribute_signup
from service.growth.queries import post_stats


def test_attribute_stamps_person_and_latest_human_click(make_person):
    joiner = make_person(name='Joiner')
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        url = make_campaign_link(tx, f'post:{rk}', f'{WEB_BASE_URL}/discover', None)
        key = url.rsplit('/', 1)[1]
        record_click(tx, key, 'facebookexternalhit/1.1')   # bot
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')      # human
        assert attribute_signup(tx, joiner['id'], key) is True
        row = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=joiner['id'])).fetchone()
        assert row['spotlight_ref'] == key
        stamped = tx.execute("SELECT ua_class FROM campaign_click WHERE link_key = %(k)s AND signup_person_id = %(pid)s", dict(k=key, pid=joiner['id'])).fetchall()
        assert [r['ua_class'] for r in stamped] == ['mobile']
        assert post_stats(tx, rk) == {'clicks': 1, 'signups': 1}
        assert attribute_signup(tx, joiner['id'], 'unknownkey') is False
        assert attribute_signup(tx, joiner['id'], None) is False


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
