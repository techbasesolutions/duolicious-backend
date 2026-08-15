"""F6 automodded bots out of deck+map; F7 defensive match guard;
F8 empty gender preference fails open like the map; F9 rewind clears
the pair's search_cache rows.
"""
from database import api_tx
from service.search.sql import (
    Q_CACHED_SEARCH,
    Q_MAP_MARKERS,
    Q_UNCACHED_SEARCH_1,
    Q_UNCACHED_SEARCH_2,
)

# Full param set Q_UNCACHED_SEARCH_2 requires, mirrored from
# service.search._uncached_search_results (the only real caller). Q_UNCACHED_SEARCH_2
# is an INSERT ... SELECT with no RETURNING, so, exactly like production,
# a deck read means: clear the cache (Q_UNCACHED_SEARCH_1), rebuild it
# (Q_UNCACHED_SEARCH_2), then read it back (Q_CACHED_SEARCH, which is where
# the `prospect_uuid` column actually lives).
DECK_PARAMS = dict(
    n=10, o=0,
    verified_only=False,
    intents=[], marital_statuses=[], has_children_buckets=[],
    assemblies=[], torah_levels=[], polygyny_stances=[], calendars=[],
    educations=[], health_tags=[],
    age_min=None, age_max=None,
    local_radius_m=160_000,
)


def _deck_uuids(tx, searcher_id, gender_pref):
    p = dict(DECK_PARAMS, searcher_person_id=searcher_id,
             gender_preference=gender_pref)
    tx.execute(Q_UNCACHED_SEARCH_1, p)
    tx.execute(Q_UNCACHED_SEARCH_2, p)
    return {r['prospect_uuid'] for r in tx.execute(Q_CACHED_SEARCH, p).fetchall()}


def test_verification_required_hidden_from_deck_and_map(make_person):
    me = make_person(name='Viewer', gender='Man')
    bot = make_person(name='Botina', gender='Woman')
    with api_tx() as tx:
        tx.execute('UPDATE person SET verification_required = TRUE WHERE id = %(p)s',
                   dict(p=bot['id']))
        # Clear every OTHER Q_MAP_MARKERS gate so the assertion below is not
        # vacuous: show_my_location defaults TRUE and coordinates are set by
        # the fixture, but citySet defaults FALSE (COALESCE(...,'FALSE')) and
        # would hide the bot on its own, making the marks assertion pass
        # whether or not the verification_required clause exists.
        tx.execute(
            "UPDATE person SET ahavah_extra = jsonb_set("
            "COALESCE(ahavah_extra, '{}'::jsonb), '{citySet}', 'true'::jsonb) "
            "WHERE id = %(p)s",
            dict(p=bot['id']),
        )
        assert bot['uuid'] not in _deck_uuids(tx, me['id'], [2])
        marks = tx.execute(Q_MAP_MARKERS, dict(
            searcher_person_id=me['id'], gender_preference=[])).fetchall()
        assert all(m['uuid'] != bot['uuid'] for m in marks)


def test_matched_pair_never_in_deck_even_without_liked_row(make_person):
    me = make_person(name='Matcher', gender='Man')
    peer = make_person(name='Matched', gender='Woman')
    with api_tx() as tx:
        tx.execute('INSERT INTO ahavah_match (user_a_id, user_b_id) VALUES '
                   '(LEAST(%(a)s,%(b)s), GREATEST(%(a)s,%(b)s))',
                   dict(a=me['id'], b=peer['id']))
        assert peer['uuid'] not in _deck_uuids(tx, me['id'], [2])


def test_empty_gender_preference_fails_open(make_person):
    me = make_person(name='Opener', gender='Man')
    w = make_person(name='Chava', gender='Woman')
    with api_tx() as tx:
        assert w['uuid'] in _deck_uuids(tx, me['id'], []), \
            'empty preference must mean "no filter", as on the map'
