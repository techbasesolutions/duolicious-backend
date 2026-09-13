from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

EXCLUDED_EMAILS = ('admin@ahavah.app',)

def _excluded() -> list[str]:
    extra = [e.strip().lower() for e in os.environ.get('AHAVAH_TEST_ACCOUNT_EMAILS', '').split(',') if e.strip()]
    return [*EXCLUDED_EMAILS, *extra]

def _last_action_sql(person_ref: str) -> str:
    """The GREATEST(...) fragment for a person's most recent like, pass or
    message, correlated to person_ref (either the %(pid)s bind or a CTE's
    p.id column reference). Kept in one place so the three queries below
    cannot drift from each other."""
    return f"""GREATEST(
               COALESCE((SELECT max(created_at) FROM liked    WHERE liker_id = {person_ref}), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM skipped  WHERE subject_person_id = {person_ref}), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM messaged WHERE subject_person_id = {person_ref}), to_timestamp(0))
             )"""

_Q_LAST_ACTION = f"""
    SELECT {_last_action_sql('%(pid)s')} AS last_action
"""

def last_action_at(tx, person_id: int) -> Optional[datetime]:
    row = tx.execute(_Q_LAST_ACTION, dict(pid=person_id)).fetchone()
    la = row['last_action'] if row else None
    return None if la is None or la.timestamp() == 0 else la

_Q_STATS_BY_GENDER = f"""
    WITH act AS (
      SELECT p.id, g.name AS gender, p.sign_up_time, p.spotlight_opt_in,
             p.subscription_expires_at,
             EXISTS (SELECT 1 FROM photo ph WHERE ph.person_id = p.id) AS has_photo,
             {_last_action_sql('p.id')} AS last_action
        FROM person p JOIN gender g ON g.id = p.gender_id
       WHERE p.activated AND lower(p.email) <> ALL(%(ex)s)
    )
    SELECT gender,
           count(*)                                                              AS members,
           count(*) FILTER (WHERE sign_up_time > NOW() - interval '7 days')      AS new_7d,
           count(*) FILTER (WHERE sign_up_time > NOW() - interval '30 days')     AS new_30d,
           count(*) FILTER (WHERE last_action > NOW() - interval '14 days')      AS acted_14d,
           count(*) FILTER (WHERE last_action < NOW() - interval '30 days' AND last_action > to_timestamp(0)) AS stale_30d,
           count(*) FILTER (WHERE last_action = to_timestamp(0))                 AS never_acted,
           count(*) FILTER (WHERE has_photo)                                     AS with_photo,
           count(*) FILTER (WHERE subscription_expires_at > NOW())               AS premium,
           count(*) FILTER (WHERE spotlight_opt_in)                              AS opted_in
      FROM act GROUP BY gender ORDER BY gender
"""

_Q_TOTALS = """
    SELECT
      (SELECT count(*) FROM ahavah_match m
        WHERE NOT EXISTS (
          SELECT 1 FROM person x WHERE x.id IN (m.user_a_id, m.user_b_id) AND lower(x.email) = ANY(%(ex)s)
        )
      ) AS matches,
      (SELECT count(*) FROM ahavah_match m
        WHERE m.created_at > NOW() - interval '30 days'
          AND NOT EXISTS (
            SELECT 1 FROM person x WHERE x.id IN (m.user_a_id, m.user_b_id) AND lower(x.email) = ANY(%(ex)s)
          )
      ) AS matches_30d,
      (SELECT count(*) FROM liked l
        WHERE NOT EXISTS (
          SELECT 1 FROM person x WHERE x.id IN (l.liker_id, l.liked_id) AND lower(x.email) = ANY(%(ex)s)
        )
      ) AS likes_total,
      (SELECT count(*) FROM liked l
        WHERE l.created_at > NOW() - interval '7 days'
          AND NOT EXISTS (
            SELECT 1 FROM person x WHERE x.id IN (l.liker_id, l.liked_id) AND lower(x.email) = ANY(%(ex)s)
          )
      ) AS likes_7d,
      (SELECT count(*) FROM messaged msg
        WHERE msg.created_at > NOW() - interval '7 days'
          AND NOT EXISTS (
            SELECT 1 FROM person x WHERE x.id IN (msg.subject_person_id, msg.object_person_id) AND lower(x.email) = ANY(%(ex)s)
          )
      ) AS msgs_7d,
      (SELECT count(*) FROM messaged msg
        WHERE msg.created_at > NOW() - interval '30 days'
          AND NOT EXISTS (
            SELECT 1 FROM person x WHERE x.id IN (msg.subject_person_id, msg.object_person_id) AND lower(x.email) = ANY(%(ex)s)
          )
      ) AS msgs_30d,
      (SELECT count(*) FROM person WHERE activated AND spotlight_opt_in AND lower(email) <> ALL(%(ex)s)) AS opted_in
"""

def growth_stats(tx) -> dict:
    ex = _excluded()
    by_gender = tx.execute(_Q_STATS_BY_GENDER, dict(ex=ex)).fetchall()
    totals = tx.execute(_Q_TOTALS, dict(ex=ex)).fetchone()
    return {'members_by_gender': [dict(r) for r in by_gender], **dict(totals)}

_Q_DORMANT = f"""
    WITH act AS (
      SELECT p.id, p.email, p.name, p.reinvite_sent_at,
             {_last_action_sql('p.id')} AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
    )
    SELECT id AS person_id, email, name, last_action
      FROM act
     WHERE last_action > to_timestamp(0)
       AND last_action < NOW() - make_interval(days => %(days)s)
       AND (reinvite_sent_at IS NULL OR reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
     ORDER BY last_action
"""

def dormant_cohort(tx, days: int = 30, resend_days: int = 30) -> list[dict]:
    return [dict(r) for r in tx.execute(_Q_DORMANT, dict(days=days, resend=resend_days, ex=_excluded())).fetchall()]

def _newcomer_predicate_sql(person_ref: str = '%(pid)s', since_ref: str = '%(since)s') -> str:
    """The WHERE predicate for 'newcomers a member would want to see': shared
    by the name-list query, the count query and the E3 recipient count so the
    three can never drift apart. Binds: %(ex)s, plus %(pid)s / %(since)s when
    the default refs are used. `person_ref` / `since_ref` let a caller
    correlate the predicate to an enclosing row instead (e.g. `d.id` and
    `d.last_action`) without re-typing it."""
    return f"""
       p.activated AND p.id <> {person_ref}
       AND lower(p.email) <> ALL(%(ex)s)
       AND p.sign_up_time > {since_ref}
       AND p.gender_id IN (SELECT gender_id FROM search_preference_gender WHERE person_id = {person_ref})
       AND (
         NOT EXISTS (SELECT 1 FROM search_preference_age a WHERE a.person_id = {person_ref})
         OR EXISTS (
           SELECT 1 FROM search_preference_age a
            WHERE a.person_id = {person_ref}
              AND date_part('year', age(p.date_of_birth)) BETWEEN COALESCE(a.min_age, 18) AND COALESCE(a.max_age, 120)
         )
       )
    """

_Q_NEWCOMERS = f"""
    SELECT split_part(p.name, ' ', 1) AS first_name, p.country, p.sign_up_time AS joined_at
      FROM person p
     WHERE {_newcomer_predicate_sql()}
     ORDER BY p.sign_up_time DESC
     LIMIT %(lim)s
"""

_Q_COUNT_NEWCOMERS = f"""
    SELECT count(*) AS n FROM person p WHERE {_newcomer_predicate_sql()}
"""

def newcomers_since(tx, person_id: int, since: datetime, limit: int = 5) -> list[dict]:
    return [dict(r) for r in tx.execute(_Q_NEWCOMERS, dict(pid=person_id, since=since, lim=limit, ex=_excluded())).fetchall()]

def count_newcomers_since(tx, person_id: int, since: datetime) -> int:
    return tx.execute(_Q_COUNT_NEWCOMERS, dict(pid=person_id, since=since, ex=_excluded())).fetchone()['n']

# E3's recipient list is "dormant members who have at least one newcomer to
# show". Counting it by materialising the cohort and running two queries per
# member is O(cohort) round trips; the admin dashboard only needs the number,
# so fold the newcomer-existence check into one statement as a correlated
# EXISTS over the SAME shared predicate the list uses.
_Q_COUNT_REINVITE_COHORT = f"""
    WITH act AS (
      SELECT p.id, p.reinvite_sent_at,
             {_last_action_sql('p.id')} AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
    )
    SELECT count(*) AS n
      FROM act d
     WHERE d.last_action > to_timestamp(0)
       AND d.last_action < NOW() - make_interval(days => %(days)s)
       AND (d.reinvite_sent_at IS NULL OR d.reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
       AND EXISTS (
         SELECT 1 FROM person p
          WHERE {_newcomer_predicate_sql('d.id', 'd.last_action')}
       )
"""

def count_reinvite_cohort(tx, days: int = 30, resend_days: int = 30) -> int:
    """How many members `emails.send_reinvite.recipients()` would return."""
    return int(tx.execute(_Q_COUNT_REINVITE_COHORT,
                          dict(days=days, resend=resend_days, ex=_excluded())).fetchone()['n'])
