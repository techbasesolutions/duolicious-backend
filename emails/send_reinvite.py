"""python -m emails.send_reinvite [--send] [--campaign-id ID]

E3 reshaped 2026-09-19 (owner decision). One campaign, two kinds of reader:

  quiet   an activated member who has not liked, passed or messaged in
          `DORMANT_DAYS`, counted from when they were last online
  paused  a member the dormancy cron deactivated after 30 days offline

Both are told how many of the people they are looking for have joined since
they were last online. Nobody is named: see emails/reinvite.py.
"""
from __future__ import annotations

import argparse, uuid

from database import api_tx
from emails.base import suppressed_sql_pattern
from emails.reinvite import reinvite_html, subject_for, SUBJECT, FROM_ADDR
from service.campaigns import (make_campaign_link, suppressed_predicate_sql,
                               unsubscribed_predicate_sql)
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded, count_newcomers_since, sought_gender_label
from service.unsubscribe import make_url as _unsub_url

UNSUB_SCOPE = 'notifications'

# A member is "quiet" once they have not liked, passed or messaged for this
# long. The dormancy cron deactivates at 30 days offline, so the two cohorts
# meet rather than overlap.
DORMANT_DAYS = 30
RESEND_DAYS = 30

# `paused` rows are not activated, so they carry no search preferences worth
# trusting for age, but their gender preference is still on file from when
# they joined. `_since` is last_online_time for both states: the owner asked
# for "since they were last online", which is also the only timestamp a
# paused member has that means anything to them.
_Q_RECIPIENTS = f"""
    WITH act AS (
      SELECT p.id, p.email, p.name, p.activated, p.reinvite_sent_at,
             COALESCE(p.last_online_time, p.sign_up_time) AS last_online,
             GREATEST(
               COALESCE((SELECT max(created_at) FROM liked    WHERE liker_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM skipped  WHERE subject_person_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM messaged WHERE subject_person_id = p.id), to_timestamp(0))
             ) AS last_action
        FROM person p
       WHERE p.deletion_requested_at IS NULL
         AND p.email IS NOT NULL AND p.email <> ''
         AND lower(p.email) <> ALL(%(ex)s)
         AND NOT ({unsubscribed_predicate_sql(UNSUB_SCOPE, 'p.id')})
         AND NOT ({suppressed_predicate_sql('p.email')})
    )
    SELECT id AS person_id, email, name, last_online,
           CASE WHEN activated THEN 'quiet' ELSE 'paused' END AS state
      FROM act
     WHERE (reinvite_sent_at IS NULL OR reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
       AND (
         NOT activated
         OR last_action < NOW() - make_interval(days => %(days)s)
       )
     ORDER BY last_online
"""


def _rows(tx) -> list[dict]:
    return [dict(r) for r in tx.execute(
        _Q_RECIPIENTS, dict(days=DORMANT_DAYS, resend=RESEND_DAYS,
                            ex=_excluded(), sup=suppressed_sql_pattern())).fetchall()]


def recipients() -> list[dict]:
    out = []
    with api_tx('read committed') as tx:
        for row in _rows(tx):
            total = count_newcomers_since(tx, row['person_id'], row['last_online'])
            if not total:
                # Nothing to report is not a reason to send. The reader hears
                # from us when something actually changed.
                continue
            row['total_new'] = int(total)
            row['gender_label'] = sought_gender_label(tx, row['person_id'])
            out.append(row)
    return out


def recipient_count() -> int:
    """Matches recipients() exactly, including the "somebody joined" rule, so
    the admin index can never promise a number a real send would not hit."""
    return len(recipients())


def build_for(row: dict) -> tuple[str, str]:
    state = row.get('state', 'quiet')
    # A paused member lands on the sign-in flow, which reactivates the
    # profile; a quiet member goes straight to the deck.
    target = f"{WEB_BASE_URL}/" if state == 'paused' else f"{WEB_BASE_URL}/discover"
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e3', target, row['person_id'] or None)
    first = (row.get('name') or 'there').split(' ')[0]
    return subject_for(state), reinvite_html(
        first, row.get('total_new', 0), cta,
        _unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL),
        gender_label=row.get('gender_label', 'new members'), state=state)


def preview_row(to: str) -> dict:
    return dict(person_id=0, email=to, name='Preview', total_new=5,
                gender_label='women', state='quiet')


def preview_row_paused(to: str) -> dict:
    return dict(preview_row(to), state='paused', total_new=10, gender_label='men')


# The name of the `service.campaigns.outbox.POST_SEND_HOOKS` entry that
# stamps `person.reinvite_sent_at`. It is a NAME, not the callable it used to
# be: the stamp has to run in the same transaction as the SMTP ACCEPTANCE,
# which now happens in the outbox drain (a different process from the run
# that queued the message), so nothing that is merely queued and later
# skipped or failed can mark a member as re-invited.
post_send = 'reinvite_sent_at'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e3-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e3', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       unsub_scope=UNSUB_SCOPE,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>",
                       post_send=post_send))


if __name__ == '__main__':
    main()
