import pytest
from pydantic import ValidationError

from database import api_tx
from service.campaigns import make_campaign_link, record_click
from service.config import WEB_BASE_URL
from service.spotlight.attribution import attribute_signup
from service.growth.queries import post_stats


def test_attribute_stamps_person_and_latest_human_click(make_person):
    joiner = make_person(name='Joiner')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'post:rk1', f'{WEB_BASE_URL}/discover', None)
        key = url.rsplit('/', 1)[1]
        record_click(tx, key, 'facebookexternalhit/1.1')   # bot
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')      # human
        assert attribute_signup(tx, joiner['id'], key) is True
        row = tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=joiner['id'])).fetchone()
        assert row['spotlight_ref'] == key
        stamped = tx.execute("SELECT ua_class FROM campaign_click WHERE link_key = %(k)s AND signup_person_id = %(pid)s", dict(k=key, pid=joiner['id'])).fetchall()
        assert [r['ua_class'] for r in stamped] == ['mobile']
        assert post_stats(tx, 'rk1') == {'clicks': 1, 'signups': 1}
        assert attribute_signup(tx, joiner['id'], 'unknownkey') is False
        assert attribute_signup(tx, joiner['id'], None) is False


def test_attribute_does_not_overwrite(make_person):
    p = make_person(name='Twice')
    with api_tx() as tx:
        a = make_campaign_link(tx, 'post:a', f'{WEB_BASE_URL}/discover', None).rsplit('/', 1)[1]
        b = make_campaign_link(tx, 'post:b', f'{WEB_BASE_URL}/discover', None).rsplit('/', 1)[1]
        record_click(tx, a, 'Mozilla/5.0'); record_click(tx, b, 'Mozilla/5.0')
        assert attribute_signup(tx, p['id'], a) is True
        assert attribute_signup(tx, p['id'], b) is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['spotlight_ref'] == a


def test_post_finish_onboarding_model_validates_spotlight_ref():
    from duotypes import PostFinishOnboarding

    assert PostFinishOnboarding().spotlight_ref is None
    assert PostFinishOnboarding(spotlight_ref='abc123').spotlight_ref == 'abc123'
    with pytest.raises(ValidationError):
        PostFinishOnboarding(spotlight_ref='x' * 40)
