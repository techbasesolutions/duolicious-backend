from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from emails.base import suppressed_sql_pattern
from service.campaigns import (PLATFORMS, suppressed_predicate_sql,
                               unsubscribed_predicate_sql)
from service.spotlight.country import display_country

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

# E3 (send_reinvite) is a `notifications`-scope campaign, so a dormant
# member must also clear the same suppression + scope-unsubscribe filters
# run_campaign() applies per-row (service/campaigns/runner.py) -- otherwise
# the admin index number (recipient_count) and the actual send list
# (dormant_cohort/recipients) would count members the runner will skip.
_Q_DORMANT = f"""
    WITH act AS (
      SELECT p.id, p.email, p.name, p.reinvite_sent_at,
             {_last_action_sql('p.id')} AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
         AND NOT ({unsubscribed_predicate_sql('notifications', 'p.id')})
         AND NOT ({suppressed_predicate_sql('p.email')})
    )
    SELECT id AS person_id, email, name, last_action
      FROM act
     WHERE last_action > to_timestamp(0)
       AND last_action < NOW() - make_interval(days => %(days)s)
       AND (reinvite_sent_at IS NULL OR reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
     ORDER BY last_action
"""

def dormant_cohort(tx, days: int = 30, resend_days: int = 30) -> list[dict]:
    return [dict(r) for r in tx.execute(
        _Q_DORMANT, dict(days=days, resend=resend_days, ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]

# Owner decision 2026-09-19: "new faces should go to all members", not only
# the ones who went quiet. Same shape as _Q_DORMANT, minus the dormancy
# window, and a member who has never liked, passed or messaged is included
# with their sign-up time standing in for a last action. The resend cap still
# holds, and the runner's own frequency cap still applies on top.
_Q_ALL_MEMBERS = f"""
    WITH act AS (
      SELECT p.id, p.email, p.name, p.reinvite_sent_at, p.sign_up_time,
             {_last_action_sql('p.id')} AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
         AND NOT ({unsubscribed_predicate_sql('notifications', 'p.id')})
         AND NOT ({suppressed_predicate_sql('p.email')})
    )
    SELECT id AS person_id, email, name,
           CASE WHEN last_action > to_timestamp(0) THEN last_action ELSE sign_up_time END AS last_action
      FROM act
     WHERE (reinvite_sent_at IS NULL OR reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
     ORDER BY last_action
"""


def all_members_cohort(tx, resend_days: int = 30) -> list[dict]:
    """Every reachable member, with the moment their "since you were here"
    counts from: their last like, pass or message, or their sign-up when they
    have never acted."""
    return [dict(r) for r in tx.execute(
        _Q_ALL_MEMBERS, dict(resend=resend_days, ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]


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
    """`p.country` is an alpha-2 code, and every caller of this puts the value
    in front of a member ("Rivka in GB"), so the rows leave here already
    naming the country."""
    return [dict(r, country=display_country(r['country']))
            for r in tx.execute(_Q_NEWCOMERS, dict(pid=person_id, since=since, lim=limit, ex=_excluded())).fetchall()]

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
             CASE WHEN {_last_action_sql('p.id')} > to_timestamp(0)
                  THEN {_last_action_sql('p.id')} ELSE p.sign_up_time END AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
         AND NOT ({unsubscribed_predicate_sql('notifications', 'p.id')})
         AND NOT ({suppressed_predicate_sql('p.email')})
    )
    SELECT count(*) AS n
      FROM act d
     WHERE (%(days)s <= 0
            OR (d.last_action > to_timestamp(0)
                AND d.last_action < NOW() - make_interval(days => %(days)s)))
       AND (d.reinvite_sent_at IS NULL OR d.reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
       AND EXISTS (
         SELECT 1 FROM person p
          WHERE {_newcomer_predicate_sql('d.id', 'd.last_action')}
       )
"""

def count_reinvite_cohort(tx, days: int = 0, resend_days: int = 30) -> int:
    """How many members `emails.send_reinvite.recipients()` would return.
    `days = 0` means every member, not only the dormant ones (owner decision
    2026-09-19); a positive value keeps the old dormancy window."""
    return int(tx.execute(_Q_COUNT_REINVITE_COHORT,
                          dict(days=days, resend=resend_days, ex=_excluded(), sup=suppressed_sql_pattern())).fetchone()['n'])

# Per-post click/sign-up attribution (spec 3.4). A post's campaign_link.kind
# is 'post:<request_key>' (see service.spotlight.attribution and the
# publishing_queue.request_key it's minted against); bot clicks are excluded
# from both counts so a crawler prefetching the link doesn't inflate either
# number, and `signups` counts distinct signup_person_id so a person who
# somehow shows up on two clicks for the same post is only counted once.
#
# Wave 3b, task 4: one campaign_link.key is shared by both platform rows of a
# post, so a click has never been attributable to Facebook or Instagram on
# its own -- everything landed in one shared total. Now that a click stores
# `campaign_click.platform` (migration 0048), the same rows are split on it
# with FILTER, in the SAME query as the totals, so the two can never drift
# apart. Every signup_person_id is credited by exactly one click row
# (attribute_signup consumes one receipt per person, first touch wins), so
# the per-platform DISTINCT counts are disjoint and always sum back to the
# top-level `signups`.
#
# Fix wave I1: the bucket names are DERIVED from `service.campaigns.PLATFORMS`
# rather than written out here, and `unknown` is that set's complement
# (`platform IS NULL OR platform NOT IN (...)`) rather than `IS NULL` alone.
# Before this, a third platform added to `PLATFORMS` would have been stored on
# real click rows, counted toward `clicks` and `signups`, and landed in no
# bucket at all, so the parts would silently stop summing to the whole.
# Writing the complement means that holds even if some other writer ever puts
# an unrecognised value in the column.

def _platform_sql_name(platform: str) -> str:
    """Guard: a platform name is spliced into this query both as a quoted
    literal and as a column alias, so only a plain lowercase identifier is
    accepted. `unknown` is refused too, since that is the complement
    bucket's own name and a collision would silently overwrite it."""
    if not platform.isidentifier() or platform.lower() != platform or platform == 'unknown':
        raise ValueError(f'bad platform name for reporting: {platform!r}')
    return platform


_PLATFORM_BUCKETS = tuple(_platform_sql_name(p) for p in PLATFORMS)
_KNOWN_PLATFORM_LITERALS = ', '.join(f"'{p}'" for p in _PLATFORM_BUCKETS)
_PLATFORM_SELECT = ''.join(
    f"""           count(*) FILTER (WHERE c.platform = '{p}')                            AS {p}_clicks,
           count(DISTINCT c.signup_person_id) FILTER (WHERE c.platform = '{p}')  AS {p}_signups,
"""
    for p in _PLATFORM_BUCKETS)
_UNKNOWN_PREDICATE = f"c.platform IS NULL OR c.platform NOT IN ({_KNOWN_PLATFORM_LITERALS})"

_Q_POST_STATS = f"""
    SELECT count(*)                                                                   AS clicks,
           count(DISTINCT c.signup_person_id)                                         AS signups,
{_PLATFORM_SELECT}           count(*) FILTER (WHERE {_UNKNOWN_PREDICATE})                       AS unknown_clicks,
           count(DISTINCT c.signup_person_id) FILTER (WHERE {_UNKNOWN_PREDICATE})     AS unknown_signups
      FROM campaign_click c
      JOIN campaign_link l ON l.key = c.link_key
     WHERE l.kind = %(kind)s AND c.ua_class <> 'bot'
"""

def post_stats(tx, request_key: str) -> dict:
    row = tx.execute(_Q_POST_STATS, dict(kind=f'post:{request_key}')).fetchone()
    return {
        'clicks': row['clicks'],
        'signups': row['signups'],
        'by_platform': {
            bucket: {'clicks': row[f'{bucket}_clicks'], 'signups': row[f'{bucket}_signups']}
            for bucket in (*_PLATFORM_BUCKETS, 'unknown')
        },
    }


# --- Gendered newcomer counts (2026-09-19) -----------------------------------
# Owner decision: until enough members have opted into Spotlight, the campaign
# emails describe new arrivals as a COUNT of the people the reader is looking
# for ("5 women joined since you were here") rather than naming anyone. Names
# and faces return once approved Spotlight cards exist to show.

_Q_SOUGHT_GENDERS = """
    SELECT g.name FROM search_preference_gender s
      JOIN gender g ON g.id = s.gender_id
     WHERE s.person_id = %(pid)s
     ORDER BY g.id
"""

# Plural forms for the gender names this product actually uses. Anything not
# listed falls back to the name itself lowercased plus 's', and an unknown or
# mixed preference degrades to the neutral word.
_PLURALS = {'Man': 'men', 'Woman': 'women'}


def sought_gender_label(tx, person_id: int, *, fallback: str = 'new members') -> str:
    """How to describe, in plural, the people this member is looking for.
    Returns `fallback` when the member seeks more than one gender or none is
    recorded, so a sentence built on it never claims something untrue."""
    names = [r['name'] for r in tx.execute(_Q_SOUGHT_GENDERS, dict(pid=person_id)).fetchall()]
    if len(names) != 1:
        return fallback
    name = names[0]
    return _PLURALS.get(name, f'{name.lower()}s')


_Q_OPPOSITE_GENDER_JOINERS = """
    SELECT count(*) AS n
      FROM person p
     WHERE p.activated
       AND p.id <> %(pid)s
       AND lower(p.email) <> ALL(%(ex)s)
       AND p.sign_up_time > %(since)s
       AND p.gender_id = %(gid)s
"""


def count_joiners_of_gender(tx, person_id: int, gender_id: int, since: datetime) -> int:
    """Activated members of one gender who joined after `since`, excluding the
    reader and the test accounts. Used for people who never finished
    onboarding, who have a gender but no search preferences yet."""
    return tx.execute(_Q_OPPOSITE_GENDER_JOINERS,
                      dict(pid=person_id, gid=gender_id, since=since, ex=_excluded())).fetchone()['n']
