from database import api_tx
from service.campaigns import can_send, log_send, make_campaign_link, record_click

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
        url = make_campaign_link(tx, 'e3', 'https://ahavah.app/discover', p['id'])
        key = url.rsplit('/', 1)[1]
        assert record_click(tx, key, 'Mozilla/5.0') == 'https://ahavah.app/discover'
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s", dict(k=key)).fetchone()['n']
        assert n == 1
        assert record_click(tx, 'nope', 'x') is None

def test_click_route_redirects(client, make_person):
    p = make_person(name='Route')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', 'https://ahavah.app/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}')
    assert r.status_code == 302 and r.headers['Location'] == 'https://ahavah.app/discover'
    assert client.get('/s/doesnotexist').status_code == 404
