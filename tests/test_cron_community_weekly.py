"""The scheduled weekly email (e2).

Two things need proving and they are different things. `is_send_window`
decides when a tick TRIES. The week campaign id decides whether that try
actually queues anything. The window is deliberately loose (the whole of
Monday from noon) because a poll never wakes exactly on the hour and a
container restart can skip an instant, so the id is what has to be tight.
"""
from datetime import datetime, timezone

from database import api_tx
from service.campaigns.runner import run_campaign
from service.campaigns.weekid import week_campaign_id
from service.cron.communityweekly import is_send_window


def _at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# 21 September 2026 is a Monday.

def test_monday_noon_utc_is_in_the_window():
    assert is_send_window(_at(2026, 9, 21, 12, 0)) is True


def test_monday_before_noon_is_not():
    assert is_send_window(_at(2026, 9, 21, 11, 59)) is False


def test_the_rest_of_monday_is_still_in_the_window():
    # The id guard stops a second send, so a late tick must still try:
    # a cron that restarted at 14:00 on Monday has to catch the week.
    assert is_send_window(_at(2026, 9, 21, 23, 59)) is True


def test_no_other_day_is():
    for day in (22, 23, 24, 25, 26, 27):
        assert is_send_window(_at(2026, 9, day, 12, 0)) is False


def _outbox_count(person_id: int) -> int:
    with api_tx('read committed') as tx:
        return tx.execute(
            "SELECT count(*) AS n FROM email_outbox WHERE person_id = %(id)s",
            dict(id=person_id)).fetchone()['n']


def _run(person, email, cid):
    return run_campaign(
        api_tx, 'e2', cid, [dict(person_id=person['id'], email=email, name='Weekly')],
        lambda row: ('This week on Ahavah', '<p>hi</p>'), send=True,
        from_addr='support@ahavah.app', unsub_scope='community', cap_days=6)


def test_a_second_tick_in_the_same_week_queues_nothing(make_person, outbox_drain):
    """The whole point of the schedule: the cron may tick many times on a
    Monday, and an operator may press Send the same week. Both compute the
    same id, and the second one must add no row. Not the window, not the
    cap: the id.

    make_person uses @example.com, which emails.base.is_suppressed_send
    blocks, so use a reserved-but-unsuppressed domain as the other campaign
    tests do."""
    p = make_person(name='Weekly')
    email = f"weekly-{p['id']}@ahavah-test.invalid"
    cid = week_campaign_id(_at(2026, 9, 21, 12), 'e2')

    first = _run(p, email, cid)
    assert first['queued'] == 1
    assert _outbox_count(p['id']) == 1

    second = _run(p, email, cid)
    assert second['queued'] == 0, 'the week id failed to dedupe a second tick'
    assert _outbox_count(p['id']) == 1


def test_the_cron_and_the_admin_frontend_agree_on_the_id():
    """Hard-coded against ahavah-admin/src/lib/growth-api.ts:565, which
    builds `cmp_${year}w${padded}_${CAMPAIGN_SUFFIX[campaign]}`. If this
    ever fails, a manual send and a scheduled send in the same week stop
    colliding and a member gets the email twice."""
    assert week_campaign_id(_at(2026, 9, 21, 12), 'e2') == 'cmp_2026w39_community'


def test_a_different_week_does_queue_again(make_person, outbox_drain):
    """Negative control for the test above. `run_campaign` increments the
    same `skipped_cap` counter for BOTH the same-run rejection and the
    frequency cap (see service/campaigns/runner.py), so a test that only
    watched the second run queue nothing could be passing because the cap
    fired. Change the id alone and the row must appear, which proves the
    id is what did the work."""
    p = make_person(name='NextWeek')
    email = f"nextweek-{p['id']}@ahavah-test.invalid"

    first = _run(p, email, week_campaign_id(_at(2026, 9, 21, 12), 'e2'))
    second = _run(p, email, week_campaign_id(_at(2026, 9, 28, 12), 'e2'))

    assert first['queued'] == 1 and second['queued'] == 1
    assert _outbox_count(p['id']) == 2
