"""Rules decided 2026-07-21/22, each shipped after a member reported a
symptom. They are enforced in SQL and in one chat helper, so a refactor
can silently revert them. These tests exist to make that loud.

  1. Only matched people can chat. Enforced server-side, not just in the
     UI (the transport used to accept a message from any member to any
     member).
  2. Views is a LOG, not a queue. A plain pass must NOT erase someone
     from the Views tabs; only a report/block hides, and it hides in
     BOTH directions. Filtering Views by pass history emptied both tabs
     for active swipers (one member had 12 real visits and saw 0).
  3. Private profiles never leak into anyone's Views lists.
"""
from __future__ import annotations

import pytest

from database import api_tx
from service.chat.chatutil import Q_IS_MATCHED
from service.person.sql import Q_VISITORS


def _match(tx, a: int, b: int) -> None:
    """Insert a confirmed match the way the app does (LEAST/GREATEST)."""
    tx.execute(
        """
        INSERT INTO ahavah_match (user_a_id, user_b_id)
        VALUES (LEAST(%(a)s, %(b)s), GREATEST(%(a)s, %(b)s))
        ON CONFLICT DO NOTHING
        """,
        dict(a=a, b=b),
    )


def _is_matched(tx, frm: int, to: int) -> bool:
    return bool(tx.execute(Q_IS_MATCHED, dict(from_id=frm, to_id=to)).fetchone())


def _visited(tx, subject: int, obj: int, invisible: bool = False) -> None:
    tx.execute(
        """
        INSERT INTO visited (subject_person_id, object_person_id, updated_at, invisible)
        VALUES (%(s)s, %(o)s, NOW(), %(inv)s)
        ON CONFLICT (subject_person_id, object_person_id)
        DO UPDATE SET updated_at = NOW(), invisible = EXCLUDED.invisible
        """,
        dict(s=subject, o=obj, inv=invisible),
    )


def _skip(tx, subject: int, obj: int, reported: bool) -> None:
    tx.execute(
        """
        INSERT INTO skipped (subject_person_id, object_person_id, reported)
        VALUES (%(s)s, %(o)s, %(r)s)
        ON CONFLICT (subject_person_id, object_person_id)
        DO UPDATE SET reported = EXCLUDED.reported
        """,
        dict(s=subject, o=obj, r=reported),
    )


def _views(tx, person_id: int) -> tuple[set[str], set[str]]:
    j = tx.execute(Q_VISITORS, dict(person_id=person_id)).fetchone()['j']
    return (
        {r['person_uuid'] for r in j['visited_you']},
        {r['person_uuid'] for r in j['you_visited']},
    )


# --- Rule 1: only matched people can chat ---------------------------------

def test_chat_match_check_is_direction_agnostic(make_person):
    """The gate must see the match from EITHER side. ahavah_match stores
    the pair LEAST/GREATEST, so a naive one-way lookup silently blocks
    whichever member sorts second."""
    a = make_person(name='Aviva', gender='Woman')
    b = make_person(name='Boaz', gender='Man')

    with api_tx() as tx:
        assert not _is_matched(tx, a['id'], b['id'])
        assert not _is_matched(tx, b['id'], a['id'])

        _match(tx, a['id'], b['id'])

        assert _is_matched(tx, a['id'], b['id']), 'matched pair blocked (a->b)'
        assert _is_matched(tx, b['id'], a['id']), 'matched pair blocked (b->a)'


def test_chat_unmatched_pair_is_not_matched(make_person):
    """Strangers must not pass the gate, even when one has viewed or
    passed the other."""
    a = make_person(name='Chana', gender='Woman')
    b = make_person(name='Dov', gender='Man')

    with api_tx() as tx:
        _visited(tx, a['id'], b['id'])
        _skip(tx, a['id'], b['id'], reported=False)
        assert not _is_matched(tx, a['id'], b['id'])


# --- Rule 2: Views is a log, not a queue ----------------------------------

def test_views_keeps_people_you_passed(make_person):
    """A plain pass must NOT remove someone from Views. This is the
    regression that emptied both tabs for active swipers."""
    me = make_person(name='Eli', gender='Man')
    other = make_person(name='Faiga', gender='Woman')

    with api_tx() as tx:
        _visited(tx, me['id'], other['id'])          # I viewed them
        _visited(tx, other['id'], me['id'])          # they viewed me
        _skip(tx, me['id'], other['id'], reported=False)  # I passed them

        visited_you, you_visited = _views(tx, me['id'])

    assert other['uuid'] in you_visited, 'a pass erased them from You viewed'
    assert other['uuid'] in visited_you, 'a pass erased them from Viewed you'


def test_views_hides_reported_people_in_both_directions(make_person):
    """Reports/blocks must hide, whichever side lodged them."""
    me = make_person(name='Gedalia', gender='Man')
    blocked_by_me = make_person(name='Hadassah', gender='Woman')
    blocked_me = make_person(name='Idit', gender='Woman')

    with api_tx() as tx:
        for p in (blocked_by_me, blocked_me):
            _visited(tx, me['id'], p['id'])
            _visited(tx, p['id'], me['id'])
        _skip(tx, me['id'], blocked_by_me['id'], reported=True)   # I reported them
        _skip(tx, blocked_me['id'], me['id'], reported=True)      # they reported me

        visited_you, you_visited = _views(tx, me['id'])

    both = visited_you | you_visited
    assert blocked_by_me['uuid'] not in both, 'person I reported still visible'
    assert blocked_me['uuid'] not in both, 'person who reported me still visible'


# --- Rule 4: deck passes are one-directional (migration 0036) -------------

def test_deck_pass_is_one_directional(make_person):
    """YOUR pass hides them from YOUR deck; THEIR pass must NOT hide
    them from your deck. A new member who idly passed a searcher used
    to erase herself from his feed before he ever saw her."""
    searcher = make_person(name='Levi', gender='Man')
    prospect = make_person(name='Miriam', gender='Woman')

    with api_tx() as tx:
        def suppressed(a, b):
            return tx.execute(
                'SELECT is_deck_suppressed(%(a)s, %(b)s) AS s',
                dict(a=a, b=b),
            ).fetchone()['s']

        # Their pass on the searcher: searcher still sees them.
        _skip(tx, prospect['id'], searcher['id'], reported=False)
        assert not suppressed(searcher['id'], prospect['id']), \
            'their pass hid them from my deck (must be one-directional)'
        # ...but their own deck no longer shows the searcher.
        assert suppressed(prospect['id'], searcher['id']), \
            "their own pass must still curate their own deck"


def test_deck_report_hides_both_directions(make_person):
    """A report is a safety barrier: it must suppress the deck BOTH
    ways, unlike a plain pass."""
    reporter = make_person(name='Noam', gender='Man')
    reported = make_person(name='Orly', gender='Woman')

    with api_tx() as tx:
        _skip(tx, reporter['id'], reported['id'], reported=True)

        def suppressed(a, b):
            return tx.execute(
                'SELECT is_deck_suppressed(%(a)s, %(b)s) AS s',
                dict(a=a, b=b),
            ).fetchone()['s']

        assert suppressed(reporter['id'], reported['id'])
        assert suppressed(reported['id'], reporter['id']), \
            'a reported pair leaked back into the reporter\'s deck'


# --- Rule 3: privacy is never leaked by Views -----------------------------

def test_views_never_leaks_a_private_profile(make_person):
    """Members with hide_me_from_strangers must not appear in anyone's
    Views lists, in either direction."""
    me = make_person(name='Yosef', gender='Man')
    private = make_person(name='Kayla', gender='Woman')

    with api_tx() as tx:
        tx.execute(
            'UPDATE person SET hide_me_from_strangers = TRUE WHERE id = %(p)s',
            dict(p=private['id']),
        )
        _visited(tx, private['id'], me['id'])   # they viewed me
        _visited(tx, me['id'], private['id'])   # I viewed them

        visited_you, you_visited = _views(tx, me['id'])

    assert private['uuid'] not in visited_you, 'private profile leaked into Viewed you'
    assert private['uuid'] not in you_visited, 'private profile leaked into You viewed'
