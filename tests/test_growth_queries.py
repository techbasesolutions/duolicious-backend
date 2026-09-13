from datetime import datetime, timedelta, timezone
from database import api_tx
from service.growth.queries import growth_stats, newcomers_since, count_newcomers_since, dormant_cohort, last_action_at

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

def _man_count(stats):
    return next((r['members'] for r in stats['members_by_gender'] if r['gender'] == 'Man'), 0)

def test_totals_and_by_gender_exclude_test_accounts(make_person, monkeypatch):
    excluded = make_person(name='ExcludedAcct', gender='Man')
    other = make_person(name='OtherAcct', gender='Woman')
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=excluded['id'])).fetchone()
    excluded_email = row['email']
    with api_tx('read committed') as tx:
        before = growth_stats(tx)
    monkeypatch.setenv('AHAVAH_TEST_ACCOUNT_EMAILS', excluded_email)
    with api_tx() as tx:
        _like(tx, excluded['id'], other['id'], 0)
        tx.execute(
            "INSERT INTO ahavah_match (user_a_id, user_b_id) VALUES (LEAST(%(a)s, %(b)s), GREATEST(%(a)s, %(b)s))",
            dict(a=excluded['id'], b=other['id']),
        )
    with api_tx('read committed') as tx:
        after = growth_stats(tx)
    assert after['likes_total'] == before['likes_total']
    assert after['matches'] == before['matches']
    assert _man_count(after) == _man_count(before) - 1

def test_dormant_cohort_excludes_never_acted(make_person):
    quiet = make_person(name='NeverActed', gender='Man')
    with api_tx('read committed') as tx:
        ids = {r['person_id'] for r in dormant_cohort(tx, days=30, resend_days=30)}
    assert quiet['id'] not in ids

def test_newcomers_respect_age_preference(make_person):
    me = make_person(name='AgePrefMe', gender='Man')
    in_range = make_person(name='InRange', gender='Woman')
    out_of_range = make_person(name='OutOfRange', gender='Woman')
    with api_tx() as tx:
        _prefers(tx, me['id'], 'Woman')
        tx.execute(
            "INSERT INTO search_preference_age (person_id, min_age, max_age) VALUES (%(p)s, 25, 35)",
            dict(p=me['id']),
        )
        tx.execute(
            "UPDATE person SET date_of_birth = (CURRENT_DATE - interval '30 years')::date WHERE id = %(id)s",
            dict(id=in_range['id']),
        )
        tx.execute(
            "UPDATE person SET date_of_birth = (CURRENT_DATE - interval '45 years')::date WHERE id = %(id)s",
            dict(id=out_of_range['id']),
        )
        since = datetime.now(timezone.utc) - timedelta(days=1)
        rows = newcomers_since(tx, me['id'], since)
    names = [r['first_name'] for r in rows]
    assert 'InRange' in names and 'OutOfRange' not in names

def test_count_matches_list_population_under_age_preference(make_person):
    me = make_person(name='CountPrefMe', gender='Man')
    in_range = make_person(name='InRangeCount', gender='Woman')
    out_of_range = make_person(name='OutOfRangeCount', gender='Woman')
    with api_tx() as tx:
        _prefers(tx, me['id'], 'Woman')
        tx.execute(
            "INSERT INTO search_preference_age (person_id, min_age, max_age) VALUES (%(p)s, 25, 35)",
            dict(p=me['id']),
        )
        tx.execute(
            "UPDATE person SET date_of_birth = (CURRENT_DATE - interval '30 years')::date WHERE id = %(id)s",
            dict(id=in_range['id']),
        )
        tx.execute(
            "UPDATE person SET date_of_birth = (CURRENT_DATE - interval '45 years')::date WHERE id = %(id)s",
            dict(id=out_of_range['id']),
        )
        since = datetime.now(timezone.utc) - timedelta(days=1)
        count = count_newcomers_since(tx, me['id'], since)
        rows = newcomers_since(tx, me['id'], since)
    assert count == 1
    assert len(rows) == 1

def test_newcomers_since_boundary(make_person):
    me = make_person(name='SinceBoundaryMe', gender='Man')
    recent = make_person(name='RecentSignup', gender='Woman')
    with api_tx() as tx:
        _prefers(tx, me['id'], 'Woman')
        tx.execute(
            "UPDATE person SET sign_up_time = NOW() - interval '3 days' WHERE id = %(id)s",
            dict(id=recent['id']),
        )
        since_narrow = datetime.now(timezone.utc) - timedelta(days=1)
        since_wide = datetime.now(timezone.utc) - timedelta(days=7)
        rows_narrow = newcomers_since(tx, me['id'], since_narrow)
        rows_wide = newcomers_since(tx, me['id'], since_wide)
    names_narrow = [r['first_name'] for r in rows_narrow]
    names_wide = [r['first_name'] for r in rows_wide]
    assert 'RecentSignup' not in names_narrow
    assert 'RecentSignup' in names_wide
