from datetime import datetime, timedelta, timezone
from database import api_tx
from service.growth.queries import growth_stats, newcomers_since, dormant_cohort, last_action_at

def _like(tx, liker, liked, days_ago):
    tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - make_interval(days => %(d)s))",
               dict(a=liker, b=liked, d=days_ago))

def _prefers(tx, pid, gender_name):
    tx.execute("INSERT INTO search_preference_gender (person_id, gender_id) SELECT %(p)s, id FROM gender WHERE name = %(g)s ON CONFLICT DO NOTHING",
               dict(p=pid, g=gender_name))

def test_stats_shape_and_exclusions(make_person):
    make_person(name='A', gender='Man'); make_person(name='B', gender='Woman')
    with api_tx('read committed') as tx:
        s = growth_stats(tx)
    assert {'members_by_gender', 'matches', 'likes_7d', 'msgs_30d', 'opted_in'} <= set(s)
    genders = {r['gender'] for r in s['members_by_gender']}
    assert genders <= {'Man', 'Woman'}

def test_dormant_cohort_edges(make_person):
    active = make_person(name='Active', gender='Man')
    stale = make_person(name='Stale', gender='Man')
    resent = make_person(name='Resent', gender='Man')
    other = make_person(name='Other', gender='Woman')
    with api_tx() as tx:
        _like(tx, active['id'], other['id'], 3)
        _like(tx, stale['id'], other['id'], 31)
        _like(tx, resent['id'], other['id'], 40)
        tx.execute("UPDATE person SET reinvite_sent_at = NOW() - interval '10 days' WHERE id = %(id)s", dict(id=resent['id']))
        ids = {r['person_id'] for r in dormant_cohort(tx, days=30, resend_days=30)}
    assert stale['id'] in ids
    assert active['id'] not in ids
    assert resent['id'] not in ids

def test_newcomers_respect_gender_preference_and_since(make_person):
    me = make_person(name='Me', gender='Man')
    new_w = make_person(name='Rivka', gender='Woman')
    new_m = make_person(name='Dan', gender='Man')
    with api_tx() as tx:
        _prefers(tx, me['id'], 'Woman')
        since = datetime.now(timezone.utc) - timedelta(days=1)
        rows = newcomers_since(tx, me['id'], since)
    names = [r['first_name'] for r in rows]
    assert 'Rivka' in names and 'Dan' not in names

def test_last_action_none_for_untouched(make_person):
    p = make_person(name='Quiet')
    with api_tx('read committed') as tx:
        assert last_action_at(tx, p['id']) is None
