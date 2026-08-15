"""F14: admin reactivation must survive autodeactivate2's next poll AND
restore the club member-count that deactivation decremented.
F15: authenticated REST traffic must count as presence (throttled), so
active members with a dead websocket aren't force-logged-out at day 30
by autodeactivate2 for looking idle."""
from __future__ import annotations

from database import api_tx
from service.api.admin.users_action_routes import _Q_REACTIVATE


def test_admin_reactivate_bumps_presence_out_of_the_cron_window(make_person):
    p = make_person(name='Sleeper', gender='Woman')
    with api_tx() as tx:
        tx.execute("UPDATE person SET activated = FALSE, "
                   "last_online_time = NOW() - INTERVAL '35 days' WHERE id = %(p)s",
                   dict(p=p['id']))
        tx.execute(_Q_REACTIVATE, dict(uuid=p['uuid']))
        row = tx.execute(
            "SELECT activated, last_online_time > NOW() - INTERVAL '1 minute' AS fresh "
            "FROM person WHERE id = %(p)s", dict(p=p['id'])).fetchone()
    assert row['activated'] and row['fresh'], \
        'reactivation left last_online_time inside the 30-50d window; ' \
        'autodeactivate2 will revert within 5 minutes'


def test_admin_reactivate_restores_club_count_exactly_once(make_person):
    """Mirrors autodeactivate2's decrement_club CTE: reactivating a
    previously-deactivated club member must re-increment count_members
    by exactly 1, and must NOT double-increment if the row was already
    activated (idempotence guard, same shape as Q_MAYBE_SIGN_IN's
    club_to_increment CTE guarded on NOT previously-activated)."""
    p = make_person(name='Clubber', gender='Woman')
    club_name = f'test-club-{p["uuid"]}'

    with api_tx() as tx:
        tx.execute(
            "INSERT INTO club (name, count_members) VALUES (%(n)s, 1)",
            dict(n=club_name),
        )
        tx.execute(
            """
            INSERT INTO person_club (person_id, club_name, activated, coordinates, gender_id)
            SELECT %(p)s, %(n)s, activated, coordinates, gender_id
            FROM person WHERE id = %(p)s
            """,
            dict(p=p['id'], n=club_name),
        )
        # Deactivate + decrement, mirroring autodeactivate2's own UPDATE.
        tx.execute(
            "UPDATE person SET activated = FALSE WHERE id = %(p)s",
            dict(p=p['id']),
        )
        tx.execute(
            "UPDATE club SET count_members = GREATEST(0, count_members - 1) "
            "WHERE name = %(n)s",
            dict(n=club_name),
        )

    try:
        with api_tx() as tx:
            count_after_deactivate = tx.execute(
                "SELECT count_members FROM club WHERE name = %(n)s",
                dict(n=club_name),
            ).fetchone()['count_members']
        assert count_after_deactivate == 0

        # Reactivate once: count must go back up by exactly 1.
        with api_tx() as tx:
            tx.execute(_Q_REACTIVATE, dict(uuid=p['uuid']))
            count_after_reactivate = tx.execute(
                "SELECT count_members FROM club WHERE name = %(n)s",
                dict(n=club_name),
            ).fetchone()['count_members']
        assert count_after_reactivate == 1, \
            'reactivation must restore the club count autodeactivate2 decremented'

        # Reactivating an ALREADY-active member must be a no-op on the
        # count, otherwise a repeat admin call (or a race) inflates it.
        with api_tx() as tx:
            tx.execute(_Q_REACTIVATE, dict(uuid=p['uuid']))
            count_after_second_reactivate = tx.execute(
                "SELECT count_members FROM club WHERE name = %(n)s",
                dict(n=club_name),
            ).fetchone()['count_members']
        assert count_after_second_reactivate == 1, \
            'reactivating an already-active member must not double-increment the club count'
    finally:
        with api_tx() as tx:
            tx.execute("DELETE FROM person_club WHERE club_name = %(n)s", dict(n=club_name))
            tx.execute("DELETE FROM club WHERE name = %(n)s", dict(n=club_name))


def test_presence_bump_helper_is_throttled(make_person):
    from service.person import bump_presence  # produced by this task
    p = make_person(name='Browser', gender='Man')
    with api_tx() as tx:
        tx.execute("UPDATE person SET last_online_time = NOW() - INTERVAL '20 minutes' "
                   "WHERE id = %(p)s", dict(p=p['id']))
    bump_presence(p['id'])
    with api_tx() as tx:
        t1 = tx.execute('SELECT last_online_time FROM person WHERE id = %(p)s',
                        dict(p=p['id'])).fetchone()['last_online_time']
    bump_presence(p['id'])  # immediate second call: throttled no-op
    with api_tx() as tx:
        t2 = tx.execute('SELECT last_online_time FROM person WHERE id = %(p)s',
                        dict(p=p['id'])).fetchone()['last_online_time']
    assert t1 == t2, 'presence bump must be throttled (one write per 10 min)'


def test_presence_bump_is_noop_for_falsy_person_id():
    from service.person import bump_presence
    # Onboardees have no person row (person_id is None on their
    # SessionInfo), must never raise or write anything.
    bump_presence(None)
