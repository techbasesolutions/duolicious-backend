"""F4: /matches must exclude blocked pairs and deactivated peers. A
reported pair must not keep exchanging presence via match cards, and a
grace-deleted (deactivated) account must not linger as a card."""
from database import api_tx
from service.decisions import Q_LIST_MATCHES, Q_GET_MATCH


def _mk_match(tx, a, b):
    return tx.execute(
        'INSERT INTO ahavah_match (user_a_id, user_b_id) '
        'VALUES (LEAST(%(a)s,%(b)s), GREATEST(%(a)s,%(b)s)) '
        'RETURNING match_id::text AS mid',
        dict(a=a, b=b)).fetchone()['mid']


def test_blocked_peer_vanishes_from_matches_both_ways(make_person):
    a = make_person(name='Ari', gender='Man')
    b = make_person(name='Bracha', gender='Woman')
    with api_tx() as tx:
        mid = _mk_match(tx, a['id'], b['id'])
        tx.execute('INSERT INTO skipped (subject_person_id, object_person_id, reported) '
                   'VALUES (%(s)s, %(o)s, TRUE)', dict(s=a['id'], o=b['id']))
        for viewer in (a['id'], b['id']):
            rows = tx.execute(Q_LIST_MATCHES, dict(me_id=viewer)).fetchall()
            assert all(r['match_id'] != mid for r in rows), \
                f'blocked match still listed for viewer {viewer}'
            got = tx.execute(Q_GET_MATCH, dict(me_id=viewer, match_id=mid)).fetchone()
            assert got is None, 'blocked match still fetchable'


def test_deactivated_peer_hidden_from_matches(make_person):
    a = make_person(name='Chaim', gender='Man')
    b = make_person(name='Dina', gender='Woman')
    with api_tx() as tx:
        mid = _mk_match(tx, a['id'], b['id'])
        tx.execute('UPDATE person SET activated = FALSE WHERE id = %(p)s', dict(p=b['id']))
        rows = tx.execute(Q_LIST_MATCHES, dict(me_id=a['id'])).fetchall()
        assert all(r['match_id'] != mid for r in rows)
