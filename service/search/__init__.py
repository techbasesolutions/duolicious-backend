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
    verified_only: bool = False,
    intents: list[str] | None = None,
    marital_statuses: list[str] | None = None,
    has_children_buckets: list[str] | None = None,
    assemblies: list[str] | None = None,
    torah_levels: list[str] | None = None,
    polygyny_stances: list[str] | None = None,
    calendars: list[str] | None = None,
    educations: list[str] | None = None,
    health_tags: list[str] | None = None,
    age_min: int | None = None,
    age_max: int | None = None,
):
    n, o = no

    params = dict(
        searcher_person_id=searcher_person_id,
        n=n,
        o=o,
        gender_preference=gender_preference,
        verified_only=verified_only,
        # Phase W cutover: pill-grid filter arrays. Empty list = no
        # filter; the SQL clauses below all guard on cardinality()=0.
        intents=intents or [],
        marital_statuses=marital_statuses or [],
        has_children_buckets=has_children_buckets or [],
        assemblies=assemblies or [],
        torah_levels=torah_levels or [],
        polygyny_stances=polygyny_stances or [],
        calendars=calendars or [],
        educations=educations or [],
        health_tags=health_tags or [],
        age_min=age_min,
        age_max=age_max,
    )

    try:
        # Per-user advisory lock — serializes concurrent /search calls
        # from the same user so the DELETE/INSERT into search_cache
        # cannot race. Without it two parallel calls both DELETE (each
        # sees 0 rows, locks nothing), the first commits its INSERT,
        # the second crashes on the search_cache_pkey UniqueViolation
        # — which segfaulted the gunicorn worker and wedged the
        # container. Different users still run /search in parallel
        # because the lock is keyed on searcher_person_id.
        tx.execute(
            "SELECT pg_advisory_xact_lock(hashtext('search:' || %(searcher_person_id)s::text))",
            dict(searcher_person_id=searcher_person_id),
        )
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
    verified_only: bool = False,
    intents: list[str] | None = None,
    marital_statuses: list[str] | None = None,
    has_children_buckets: list[str] | None = None,
    assemblies: list[str] | None = None,
    torah_levels: list[str] | None = None,
    polygyny_stances: list[str] | None = None,
    calendars: list[str] | None = None,
    educations: list[str] | None = None,
    health_tags: list[str] | None = None,
    age_min: int | None = None,
    age_max: int | None = None,
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
                gender_preference=gender_preference,
                verified_only=verified_only,
                intents=intents,
                marital_statuses=marital_statuses,
                has_children_buckets=has_children_buckets,
                assemblies=assemblies,
                torah_levels=torah_levels,
                polygyny_stances=polygyny_stances,
                calendars=calendars,
                educations=educations,
                health_tags=health_tags,
                age_min=age_min,
                age_max=age_max,
            )

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
