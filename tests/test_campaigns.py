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
        target, receipt = record_click(tx, key, 'Mozilla/5.0')
        assert target == f'{WEB_BASE_URL}/discover' and receipt
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s", dict(k=key)).fetchone()['n']
        assert n == 1
        assert record_click(tx, 'nope', 'x') == (None, None)

def test_click_route_redirects(client, make_person):
    p = make_person(name='Route')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', f'{WEB_BASE_URL}/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}')
    assert r.status_code == 302 and r.headers['Location'] == f'{WEB_BASE_URL}/discover'
    assert client.get('/s/doesnotexist').status_code == 404


# ---------------------------------------------------------------------------
# Wave 3b, task 2: the route hands the click's receipt back to the web
# forwarder as a response header (never a cookie -- the web app owns that),
# and forwards `?p=` into `record_click`'s platform argument. A bot never
# gets a header at all, so a crawler can never carry a usable receipt.
# ---------------------------------------------------------------------------

def test_click_route_sets_receipt_header_and_forwards_platform(client, make_person):
    p = make_person(name='Receipt')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', f'{WEB_BASE_URL}/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}?p=instagram', headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
    assert r.status_code == 302 and 'X-Spotlight-Receipt' in r.headers
    receipt = r.headers['X-Spotlight-Receipt']
    with api_tx() as tx:
        row = tx.execute("SELECT platform FROM campaign_click WHERE receipt = %(r)s", dict(r=receipt)).fetchone()
    assert row['platform'] == 'instagram'


def test_click_route_omits_receipt_header_for_a_bot(client, make_person):
    p = make_person(name='BotRoute')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', f'{WEB_BASE_URL}/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}', headers={'User-Agent': 'facebookexternalhit/1.1'})
    assert r.status_code == 302 and 'X-Spotlight-Receipt' not in r.headers


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


# ---------------------------------------------------------------------------
# Final review: a bare `startswith(WEB_BASE_URL)` host guard is bypassed by a
# subdomain suffix attack -- 'https://ahavah.app.evil.example/x' literally
# starts with 'https://ahavah.app'. The guard must compare scheme + netloc.
# ---------------------------------------------------------------------------

def test_campaign_link_rejects_a_lookalike_subdomain_target(make_person):
    p = make_person(name='Lookalike')
    with api_tx() as tx:
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e1', 'https://ahavah.app.evil.example/x', p['id'])
        # the existing, legitimate on-site target must still be accepted
        url = make_campaign_link(tx, 'e1', f'{WEB_BASE_URL}/discover', p['id'])
        assert url.startswith(f"{WEB_BASE_URL.rstrip('/')}/s/")


# ---------------------------------------------------------------------------
# Fix round 1: E5's share CTA must be able to target the live Facebook/
# Instagram post, which is off WEB_BASE_URL. `external_ok=True` opens that
# one exception -- still scheme+netloc pinned, never a bare prefix match, and
# still False (unchanged behaviour) unless a caller opts in.
# ---------------------------------------------------------------------------

def test_campaign_link_external_ok_accepts_allowed_host_only_when_opted_in(make_person):
    p = make_person(name='External')
    with api_tx() as tx:
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e5', 'https://www.facebook.com/123', p['id'])
        url = make_campaign_link(tx, 'e5', 'https://www.facebook.com/123', p['id'], external_ok=True)
        assert url.startswith(f"{WEB_BASE_URL.rstrip('/')}/s/")


def test_campaign_link_external_ok_still_rejects_lookalike_and_insecure(make_person):
    p = make_person(name='ExternalLookalike')
    with api_tx() as tx:
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e5', 'https://www.facebook.com.evil.example/x', p['id'], external_ok=True)
        with pytest.raises(ValueError):
            make_campaign_link(tx, 'e5', 'http://www.facebook.com/x', p['id'], external_ok=True)
