"""
service.search — discovery query service.

Task 0.3e (Q&A subsystem strip per audit) removed:
  - Q_QUIZ_SEARCH import + `_quiz_search_results` helper + 'quiz-search' branch
    in `get_search_type` and `get_search`.

The remaining `_uncached_search_results` and `_cached_search_results` helpers
now operate on the stub SQL fragments in `service/search/sql/__init__.py`.

Phase 1 Task 1.1 will rewrite these with country/language/verification
ranking once `bumpy-design-tokens` and the new `swipe` table land.
"""

import psycopg
import duotypes as t
from database import api_tx
from typing import Tuple
from service.search.sql import (
    Q_CACHED_SEARCH,
    Q_SEARCH_PREFERENCE,
    Q_UNCACHED_SEARCH_1,
    Q_UNCACHED_SEARCH_2,
    Q_FEED,
)
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ClubHttpArg:
    club: str | None


def _uncached_search_results(
    tx,
    searcher_person_id: int,
    no: Tuple[int, int],
    gender_preference: list[int],
):
    n, o = no

    params = dict(
        searcher_person_id=searcher_person_id,
        n=n,
        o=o,
        gender_preference=gender_preference,
    )

    try:
        tx.execute(Q_UNCACHED_SEARCH_1, params)
        tx.execute(Q_UNCACHED_SEARCH_2, params)
        tx.execute(Q_CACHED_SEARCH, params)
        return tx.fetchall()
    except psycopg.errors.QueryCanceled:
        # The query probably timed-out because it was too specific
        return []


def _cached_search_results(tx, searcher_person_id: int, no: Tuple[int, int]):
    n, o = no

    params = dict(
        searcher_person_id=searcher_person_id,
        n=n,
        o=o
    )

    return tx.execute(Q_CACHED_SEARCH, params).fetchall()


def get_search_type(n: str | None, o: str | None):
    """Default to uncached-search. The original 'quiz-search' branch (returned
    when n/o were None) was Q&A-driven and removed in 0.3e. Callers passing
    no pagination now get the first page of uncached-search."""
    n_: int | None = n if n is None else int(n)
    o_: int | None = o if o is None else int(o)

    if n_ is not None and not n_ >= 0:
        raise ValueError('n must be >= 0')
    if o_ is not None and not o_ >= 0:
        raise ValueError('o must be >= 0')

    # Default first-page when caller didn't paginate.
    if n_ is None:
        n_ = 10
    if o_ is None:
        o_ = 0

    no = (n_, o_)

    if no[1] == 0:
        return 'uncached-search', no
    else:
        return 'cached-search', no


def get_search(
    s: t.SessionInfo,
    n: str | None,
    o: str | None,
    club: ClubHttpArg | None,
):
    search_type, no = get_search_type(n, o)

    if no is not None and no[0] > 10:
        return 'n must be less than or equal to 10', 400

    if s.person_id is None:
        return '', 500

    params = dict(
        person_id=s.person_id,
        club_name=club.club if club else None,
        do_modify=club is not None,
    )

    with api_tx('READ COMMITTED') as tx:
        tx.execute('SET LOCAL statement_timeout = 10000') # 10 seconds

        rows = tx.execute(Q_SEARCH_PREFERENCE, params).fetchall()

        gender_preference = [row['gender_id'] for row in rows]

        if search_type == 'uncached-search':
            return _uncached_search_results(
                tx=tx,
                searcher_person_id=s.person_id,
                no=no,
                gender_preference=gender_preference)

        elif search_type == 'cached-search':
            return _cached_search_results(
                tx=tx,
                searcher_person_id=s.person_id, no=no)

        else:
            raise Exception(f'Unexpected search type: {search_type}')


def get_feed(s: t.SessionInfo, before: datetime):
    params = dict(
        searcher_person_id=s.person_id,
        before=before,
    )

    with api_tx('READ COMMITTED') as tx:
        rows = tx.execute(Q_FEED, params).fetchall()

    return [row['j'] for row in rows]
