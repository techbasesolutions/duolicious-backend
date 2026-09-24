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
from service.campaigns.schedule import is_send_window, next_send_at


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


def test_a_second_tick_before_the_first_is_delivered_queues_nothing(make_person, outbox_drain):
    """The cron may tick many times on a Monday while the outbox is still
    draining the first batch. Nothing has been ACCEPTED yet at that point, so
    there is no email_send_log row and `_Q_SAME_RUN` cannot help. What holds
    here is the outbox's own unique key on
    (campaign, campaign_id, person_id): `enqueue` returns None on conflict.

    Named for that mechanism rather than for the id guard generally, because
    the two are different and only one of them is load bearing in this
    window. The id guard proper is the test below.

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


def test_the_week_id_format_is_pinned():
    """A regression lock on THIS repo's half of the pair only.

    The two sides are genuinely compared in
    ahavah-admin/tests/growth-api.test.mjs, which reads this module from
    disk and fails loudly on a divergence. That is the test that matters,
    and it is the one that caught the real divergence on 2026-09-24, when
    this module shipped 'community' and 'reinvite' against the admin's
    established 'comm' and 'reinv'.

    What used to stand here was a docstring claiming to check the admin repo
    over an assertion comparing the Python function to its own output. It
    passed the whole time the two sides disagreed. It is kept, honestly
    named, only so a change to the id FORMAT is visible from inside this
    repo without a checkout of the other one."""
    assert week_campaign_id(_at(2026, 9, 21, 12), 'e2') == 'cmp_2026w39_comm'


def test_the_id_guard_holds_once_the_first_batch_has_been_delivered(make_person, outbox_drain):
    """The guard the plan and the cron docstring actually name: `_Q_SAME_RUN`
    in service/campaigns/__init__.py, which reads email_send_log.

    That table is only written when the drain ACCEPTS a message, so this test
    drains before re-running. It then passes cap_days=0 to take the 6-day
    frequency cap out of the picture entirely, because `can_send` folds both
    checks into one `skipped_cap` counter and a test that left the cap on
    could not say which of the two did the work. With the cap disabled, the
    only thing that can stop the second run is the id.
    """
    p = make_person(name='Delivered')
    email = f"delivered-{p['id']}@ahavah-test.invalid"
    cid = week_campaign_id(_at(2026, 9, 21, 12), 'e2')

    assert _run(p, email, cid)['queued'] == 1
    assert len(outbox_drain(p['id'])) == 1, 'the drain did not deliver the first batch'
    with api_tx('read committed') as tx:
        assert tx.execute(
            "SELECT count(*) AS n FROM email_send_log WHERE person_id = %(id)s",
            dict(id=p['id'])).fetchone()['n'] == 1

    again = run_campaign(
        api_tx, 'e2', cid, [dict(person_id=p['id'], email=email, name='Delivered')],
        lambda row: ('This week on Ahavah', '<p>hi</p>'), send=True,
        from_addr='support@ahavah.app', unsub_scope='community', cap_days=0)
    assert again['queued'] == 0, 'the same-run guard let a delivered week through'


def test_the_id_guard_is_what_blocks_it_and_not_the_row_already_existing(make_person, outbox_drain):
    """Negative control for the test above. Same shape, same drain, same
    cap_days=0, only the id differs. If this queued nothing either, the test
    above would be proving something other than the id."""
    p = make_person(name='Delivered2')
    email = f"delivered2-{p['id']}@ahavah-test.invalid"

    assert _run(p, email, week_campaign_id(_at(2026, 9, 21, 12), 'e2'))['queued'] == 1
    assert len(outbox_drain(p['id'])) == 1

    other = run_campaign(
        api_tx, 'e2', week_campaign_id(_at(2026, 9, 28, 12), 'e2'),
        [dict(person_id=p['id'], email=email, name='Delivered2')],
        lambda row: ('This week on Ahavah', '<p>hi</p>'), send=True,
        from_addr='support@ahavah.app', unsub_scope='community', cap_days=0)
    assert other['queued'] == 1


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


# ---------------------------------------------------------------------------
# next_send_at, which is what the Growth > Emails row prints. The window and
# the next slot have to disagree in exactly one place: inside the window,
# because there the send has either just happened or is happening on this
# tick, and "next send in four hours" would be worse than saying nothing.
# ---------------------------------------------------------------------------

def test_from_midweek_the_next_slot_is_the_coming_monday():
    # Thursday 24 September 2026.
    assert next_send_at(_at(2026, 9, 24, 9)) == _at(2026, 9, 28, 12)


def test_from_inside_the_window_the_next_slot_is_next_week():
    # Monday 13:00, one hour into the window: this week's send is done or
    # in flight, so the next one is a week out.
    assert next_send_at(_at(2026, 9, 21, 13)) == _at(2026, 9, 28, 12)


def test_from_monday_morning_the_next_slot_is_today():
    assert next_send_at(_at(2026, 9, 21, 9)) == _at(2026, 9, 21, 12)


def test_the_window_and_the_next_slot_never_both_point_at_now():
    """Walk a whole week hour by hour. At every hour, the next slot must be
    strictly in the future, and it must never land inside the window it was
    computed from."""
    for day in range(21, 28):
        for hour in range(24):
            now = _at(2026, 9, day, hour)
            nxt = next_send_at(now)
            assert nxt > now, (now, nxt)
            if is_send_window(now):
                assert nxt > now, (now, nxt)


# ---------------------------------------------------------------------------
# What the Growth > Emails row is told. Only e2 is on a cadence; e1 and e3
# are operator-run by design.
# ---------------------------------------------------------------------------

def test_only_the_weekly_email_carries_a_schedule():
    from service.api.admin.growth_routes import _schedule_for
    now = _at(2026, 9, 24, 9)
    assert _schedule_for('e2', now, None) is not None
    for other in ('e1', 'e3', 'e4', 'e5'):
        assert _schedule_for(other, now, None) is None


def test_a_disabled_schedule_offers_no_next_send_date():
    """Off is the default. A row that printed a next send date while nothing
    was running would recreate the exact problem this work exists to fix."""
    from service.api.admin.growth_routes import _schedule_for
    import service.campaigns.schedule as sched
    s = _schedule_for('e2', _at(2026, 9, 24, 9), None)
    assert s is not None
    assert s['enabled'] is sched.COMMUNITY_WEEKLY_ENABLED
    if not sched.COMMUNITY_WEEKLY_ENABLED:
        assert s['next_send_at'] is None
        # Nothing is running, so nothing can be late. "Overdue" next to
        # "not scheduled" would be two different explanations for one row.
        assert s['overdue'] is False


def test_an_enabled_schedule_reports_a_week_that_never_went_out(monkeypatch):
    """The mirror of the defect this whole branch exists to fix. A cron
    container down for the whole Monday window loses the week in silence: no
    rows, no error, nothing logged, while the row goes on promising "Next
    Monday, 08:00" and the last-sent date quietly stops advancing."""
    import service.api.admin.growth_routes as gr
    monkeypatch.setattr(gr, 'COMMUNITY_WEEKLY_ENABLED', True)
    now = _at(2026, 9, 24, 9)

    fresh = gr._schedule_for('e2', now, _at(2026, 9, 21, 12))
    assert fresh['overdue'] is False, 'a send three days ago is not overdue'

    stale = gr._schedule_for('e2', now, _at(2026, 9, 14, 12))
    assert stale['overdue'] is True, 'a missed week went unreported'

    assert gr._schedule_for('e2', now, None)['overdue'] is True


def test_a_late_send_inside_the_window_is_not_overdue(monkeypatch):
    """Negative control on the threshold. The window runs to midnight, so a
    send can legitimately land almost 12 hours after the slot. Eight days is
    one cadence plus a day of slack precisely so that never trips."""
    import service.api.admin.growth_routes as gr
    monkeypatch.setattr(gr, 'COMMUNITY_WEEKLY_ENABLED', True)
    # Sent 23:59 on Monday; it is now Monday 11:00 a week later, one hour
    # before the next slot. That is the longest healthy gap there is.
    s = gr._schedule_for('e2', _at(2026, 9, 28, 11), _at(2026, 9, 21, 23, 59))
    assert s['overdue'] is False
