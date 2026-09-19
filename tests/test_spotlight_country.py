"""`person.country` holds an ISO 3166 alpha-2 code in production, and every
Spotlight surface used to show it raw: the rendered welcome card read "BB"
under the member's name and the caption read "Welcome to Ahavah, Ehud. BB."

`service.spotlight.country.display_country` is the one place a code becomes a
short, natural country name, and these tests pin it at each surface that
shows a country to a member, an operator or the public.

`_make_eligible` is copied from tests/test_spotlight_card.py on purpose: test
files in this suite do not import from each other.
"""
from __future__ import annotations

import hashlib
import secrets

import pytest

from database import api_tx
from service.spotlight.approval import make_card_token
from service.spotlight.country import display_country
from service.spotlight.queue import create_candidate
from service.spotlight.roundup import roundup_snapshot

H = {'X-Growth-Cron': 'test-cron-secret'}


def _make_eligible(make_person, name='CountryElig', gender='Woman', country=None):
    p = make_person(name=name, gender=gender)
    with api_tx() as tx:
        tx.execute("""
            UPDATE person SET spotlight_opt_in = TRUE, spotlight_opt_in_at = NOW(),
                   ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01',
                   deletion_requested_at = NULL, spotlight_last_featured_at = NULL,
                   country = %(c)s
             WHERE id = %(id)s""", dict(id=p['id'], c=country))
        tx.execute("""
            INSERT INTO photo (uuid, person_id, position, moderation_status, blurhash, hash)
            VALUES (%(u)s, %(id)s, 1, 'approved', 'testblurhash', gen_random_uuid()::text)""",
                   dict(u=secrets.token_hex(32), id=p['id']))
    return p


def _make_admin(make_person):
    p = make_person(name='CountryAdmin')
    with api_tx() as tx:
        tx.execute("UPDATE person SET roles = ARRAY['admin']::TEXT[] WHERE id = %(i)s",
                   dict(i=p['id']))
    return p


def _session_for(p) -> str:
    """A real signed-in duo_session row, returned as its bearer token. The
    two routes below are session-only (`@apost`/`@aget`), not cron-callable."""
    tok = secrets.token_hex(32)
    with api_tx() as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(i)s",
                           dict(i=p['id'])).fetchone()['email']
        tx.execute(
            """INSERT INTO duo_session (session_token_hash, email, person_id, signed_in, otp)
               VALUES (%(h)s, %(e)s, %(p)s, TRUE, '123456')""",
            dict(h=hashlib.sha512(tok.encode()).hexdigest(), e=email, p=p['id']))
    return tok


def _email(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT email FROM person WHERE id = %(id)s",
                          dict(id=pid)).fetchone()['email']


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('code,expected', [
    ('BB', 'Barbados'),
    ('bb', 'Barbados'),
    (' Bb ', 'Barbados'),
    ('US', 'United States'),
    ('GB', 'United Kingdom'),
    ('CA', 'Canada'),
    ('NG', 'Nigeria'),
    # pycountry carries a common_name for these; it is preferred over `name`.
    ('BO', 'Bolivia'),
    ('VE', 'Venezuela'),
    # The formal tails pycountry uses, cut back to what a member would say.
    ('KR', 'South Korea'),
    ('KP', 'North Korea'),
    ('TZ', 'Tanzania'),
    ('MD', 'Moldova'),
    ('IR', 'Iran'),
    ('RU', 'Russia'),
    ('SY', 'Syria'),
    ('LA', 'Laos'),
    ('CD', 'DR Congo'),
    ('PS', 'Palestine'),
    ('TW', 'Taiwan'),
    ('FM', 'Micronesia'),
    ('VN', 'Vietnam'),
    ('TR', 'Türkiye'),
])
def test_display_country_resolves_codes_to_short_natural_names(code, expected):
    assert display_country(code) == expected


@pytest.mark.parametrize('value', [
    None,
    '',
    'Barbados',
    'Warrens, Barbados',
    'ZZ',            # two letters, but no such country
    'B',             # too short
    'BBB',           # too long
    '12',            # not alphabetic
    'B1',
])
def test_display_country_leaves_everything_else_unchanged(value):
    assert display_country(value) == value


def test_display_country_never_raises_without_pycountry(monkeypatch):
    """An import failure must degrade to the raw value, never to a 500 on the
    member's own card screen."""
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name == 'pycountry':
            raise ImportError('no pycountry')
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, '__import__', _boom)
    assert display_country('BB') == 'BB'


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------

def test_welcome_caption_names_the_country(client, make_person, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_enqueue_card_ready', lambda tx, pid, rk: None)
    p = _make_eligible(make_person, name='Ehud', gender='Man', country='BB')
    r = client.post('/admin/growth/spotlight/welcome', json={'person_id': p['id']}, headers=H)
    assert r.status_code == 200
    rk = r.get_json()['request_key']
    with api_tx('read committed') as tx:
        caption = tx.execute(
            "SELECT caption FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform LIMIT 1",
            dict(rk=rk)).fetchone()['caption']
    assert 'Welcome to Ahavah, Ehud. Barbados.' in caption
    assert ' BB' not in caption


def test_member_of_week_caption_names_the_country(client, make_person, monkeypatch):
    import service.api.admin.spotlight_routes as sr
    monkeypatch.setattr(sr, '_enqueue_card_ready', lambda tx, pid, rk: None)
    admin = _make_admin(make_person)
    A = {'Authorization': f'Bearer {_session_for(admin)}'}
    p = _make_eligible(make_person, name='Rivka', gender='Woman', country='NG')
    r = client.post('/admin/growth/spotlight/member-of-week', json={'person_id': p['id']}, headers=A)
    assert r.status_code == 200
    rk = r.get_json()['request_key']
    with api_tx('read committed') as tx:
        caption = tx.execute(
            "SELECT caption FROM publishing_queue WHERE request_key = %(rk)s ORDER BY platform LIMIT 1",
            dict(rk=rk)).fetchone()['caption']
    assert 'Member of the week: Rivka,' in caption and 'Nigeria.' in caption
    assert ' NG' not in caption


def test_queue_listing_row_names_the_country(client, make_person):
    p = _make_eligible(make_person, name='QueueRow', country='BB')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'],
                              caption='c', created_by='t')
    rows = client.get('/admin/growth/queue', headers=H).get_json()
    mine = [r for r in rows if r['request_key'] == rk]
    assert mine
    assert all(r['subject']['country'] == 'Barbados' for r in mine)


def test_member_card_get_names_the_country(client, make_person):
    p = _make_eligible(make_person, name='CardGet', country='BB')
    with api_tx() as tx:
        rk = create_candidate(tx, kind='welcome', subject_person_id=p['id'],
                              caption='c', created_by='t')
    # `_email` opens its own transaction, so it is read BEFORE the next one
    # is entered: the api connection lock is not reentrant.
    email = _email(p['id'])
    with api_tx() as tx:
        tok = make_card_token(tx, rk, email)
    r = client.get(f'/spotlight/card/{tok}')
    assert r.status_code == 200
    assert r.get_json()['country'] == 'Barbados'


def test_suggest_row_names_the_country(client, make_person):
    """`/admin/growth/spotlight/suggest` returns the top three candidates of a
    database this suite shares, so the person under test is sorted to the
    front deliberately: the query puts the gender OPPOSITE the most recently
    featured one first, then never-featured members oldest opt-in first."""
    admin = _make_admin(make_person)
    A = {'Authorization': f'Bearer {_session_for(admin)}'}
    with api_tx('read committed') as tx:
        last = tx.execute(
            """SELECT g.name AS gender
                 FROM (SELECT person_id, MAX(created_at) AS lf
                         FROM spotlight_occurrence GROUP BY person_id) o
                 JOIN person p ON p.id = o.person_id
                 JOIN gender g ON g.id = p.gender_id
                ORDER BY o.lf DESC LIMIT 1""").fetchone()
    gender = 'Man' if (last and last['gender'] == 'Woman') else 'Woman'
    p = _make_eligible(make_person, name='Suggested', gender=gender, country='BB')
    with api_tx() as tx:
        tx.execute("UPDATE person SET spotlight_opt_in_at = '1970-01-01' WHERE id = %(i)s",
                   dict(i=p['id']))
    rows = client.get('/admin/growth/spotlight/suggest', headers=A).get_json()
    assert rows
    row = next(r for r in rows if r['person_id'] == p['id'])
    assert row['country'] == 'Barbados'
    assert 'Barbados.' in row['suggested_caption']


def test_roundup_snapshot_countries_are_names(make_person):
    a = _make_eligible(make_person, name='RoundupBB', country='BB')
    _make_eligible(make_person, name='RoundupGB', country='GB')
    with api_tx() as tx:
        snap = roundup_snapshot(tx, days=7)
    assert 'Barbados' in snap['country_names']
    assert 'United Kingdom' in snap['country_names']
    assert all(len(n) != 2 for n in snap['country_names'])
    # The tile count is unaffected and the distinct count still counts
    # distinct countries, now counted over the mapped names.
    assert snap['countries'] == len(set(snap['country_names']))
    assert snap['countries'] >= 2
    assert a['id'] is not None


def test_weekly_email_names_the_country():
    from emails.community_weekly import community_weekly_html
    html = community_weekly_html(
        [dict(first_name='Ehud', country='BB')],
        total_members=10,
        spotlight=dict(first_name='Rivka', age=30, country='NG',
                       image_url='https://cdn/x.png', post_url='https://fb/1'),
        cta_url='https://ahavah.example/discover',
        unsubscribe_url='https://ahavah.example/u')
    assert 'in Barbados' in html
    assert 'in Nigeria' in html
    assert 'in BB' not in html and 'in NG' not in html


def test_newcomers_rows_name_the_country(make_person):
    from datetime import datetime, timedelta, timezone

    from service.growth.queries import newcomers_since

    me = make_person(name='NewcomerReader', gender='Man')
    _make_eligible(make_person, name='NewcomerBB', gender='Woman', country='BB')
    with api_tx() as tx:
        tx.execute(
            """INSERT INTO search_preference_gender (person_id, gender_id)
               SELECT %(p)s, id FROM gender WHERE name = 'Woman'
               ON CONFLICT DO NOTHING""", dict(p=me['id']))
        tx.execute("UPDATE person SET activated = TRUE WHERE id = %(p)s", dict(p=me['id']))
        rows = newcomers_since(tx, me['id'], datetime.now(timezone.utc) - timedelta(days=1), limit=50)
    assert rows
    assert all(r['country'] is None or len(r['country']) != 2 for r in rows)
    assert any(r['country'] == 'Barbados' for r in rows)
