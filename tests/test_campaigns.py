import pytest

from database import api_tx
from service.campaigns import _ua_class, can_send, log_send, make_campaign_link, record_click
from service.config import WEB_BASE_URL

def test_cap_blocks_second_campaign_within_seven_days(make_person):
    p = make_person(name='Cap')
    with api_tx() as tx:
        assert can_send(tx, p['id'], 'e1', 'run-a')
        log_send(tx, p['id'], 'e1', 'run-a', 'mid-1')
        assert not can_send(tx, p['id'], 'e1', 'run-a')          # same run, idempotent
        assert not can_send(tx, p['id'], 'e3', 'run-b')          # cap
        assert can_send(tx, p['id'], 'e4', 'run-c', exempt=True) # member-triggered

def test_cap_expires_after_window(make_person):
    p = make_person(name='Old')
    with api_tx() as tx:
        log_send(tx, p['id'], 'e1', 'run-old', 'mid')
        tx.execute("UPDATE email_send_log SET sent_at = NOW() - interval '8 days' WHERE person_id = %(id)s", dict(id=p['id']))
        assert can_send(tx, p['id'], 'e3', 'run-new')

def test_campaign_link_roundtrip(make_person):
    p = make_person(name='Link')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e3', f'{WEB_BASE_URL}/discover', p['id'])
        key = url.rsplit('/', 1)[1]
        assert record_click(tx, key, 'Mozilla/5.0') == f'{WEB_BASE_URL}/discover'
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s", dict(k=key)).fetchone()['n']
        assert n == 1
        assert record_click(tx, 'nope', 'x') is None

def test_click_route_redirects(client, make_person):
    p = make_person(name='Route')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', f'{WEB_BASE_URL}/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}')
    assert r.status_code == 302 and r.headers['Location'] == f'{WEB_BASE_URL}/discover'
    assert client.get('/s/doesnotexist').status_code == 404


# ---------------------------------------------------------------------------
# M-b: _ua_class buckets the click user agent. Tested directly so the four
# branches cannot silently drift.
# ---------------------------------------------------------------------------

def test_ua_class_buckets():
    assert _ua_class('facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)') == 'bot'
    assert _ua_class('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15') == 'mobile'
    assert _ua_class('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36') == 'desktop'
    assert _ua_class('') == 'unknown'
    assert _ua_class(None) == 'unknown'


# ---------------------------------------------------------------------------
# M-f: /s/<key> is an open redirect unless the target is pinned to our own
# web app, so make_campaign_link refuses anything off WEB_BASE_URL.
# ---------------------------------------------------------------------------

def test_campaign_link_rejects_off_site_target(make_person):
    p = make_person(name='OffSite')
    with api_tx() as tx:
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e1', 'https://evil.example.com/phish', p['id'])
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e1', '/discover', p['id'])
