"""F1: match creation must be derivable from CURRENT state, not from
whether this call's INSERT won. Repro of the live hazard: the liked
half-rows both exist (concurrent inserts) but no match row was made;
a re-like must repair it."""
from database import api_tx
from service.decisions import Q_RECORD_LIKE


def _like(tx, me_id: int, prospect_uuid: str):
    return tx.execute(Q_RECORD_LIKE, dict(
        me_id=me_id, prospect_uuid=prospect_uuid)).fetchone()


def _uuid_of(tx, pid):
    return tx.execute('SELECT uuid::text AS u FROM person WHERE id = %(p)s',
                      dict(p=pid)).fetchone()['u']


def test_relike_repairs_a_matchless_mutual_like(make_person):
    a = make_person(name='Aleph', gender='Man')
    b = make_person(name='Bet', gender='Woman')
    with api_tx() as tx:
        # Simulate the REPEATABLE READ race: both half-rows exist,
        # no match row (exactly what two concurrent likes produce).
        tx.execute(
            'INSERT INTO liked (liker_id, liked_id) VALUES '
            '(%(a)s, %(b)s), (%(b)s, %(a)s)', dict(a=a['id'], b=b['id']))
        row = _like(tx, a['id'], _uuid_of(tx, b['id']))
        assert row is not None and row['match_id'] is not None, \
            're-like on a matchless mutual pair must create the match'
        assert row['was_new_like'] is False
        n = tx.execute('SELECT count(*) AS n FROM ahavah_match WHERE '
                       '(user_a_id = LEAST(%(a)s,%(b)s) AND user_b_id = '
                       'GREATEST(%(a)s,%(b)s))',
                       dict(a=a['id'], b=b['id'])).fetchone()['n']
        assert n == 1


def test_duplicate_like_returns_existing_match_and_flags_not_new(make_person):
    a = make_person(name='Gimel', gender='Man')
    b = make_person(name='Dalet', gender='Woman')
    with api_tx() as tx:
        b_uuid = _uuid_of(tx, b['id'])
        a_uuid = _uuid_of(tx, a['id'])
        first = _like(tx, a['id'], b_uuid)
        assert first['match_id'] is None and first['was_new_like'] is True
        second = _like(tx, b['id'], a_uuid)
        assert second['match_id'] is not None and second['was_new_like']
        again = _like(tx, a['id'], b_uuid)
        assert again['match_id'] == second['match_id'], \
            'duplicate like must return the existing match, not null'
        assert again['was_new_like'] is False


def test_one_sided_duplicate_like_stays_matchless(make_person):
    a = make_person(name='He', gender='Man')
    b = make_person(name='Vav', gender='Woman')
    with api_tx() as tx:
        b_uuid = _uuid_of(tx, b['id'])
        _like(tx, a['id'], b_uuid)
        row = _like(tx, a['id'], b_uuid)
        assert row['match_id'] is None and row['was_new_like'] is False
