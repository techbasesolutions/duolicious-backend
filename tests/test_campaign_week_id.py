"""The week id is the scheduled send's only real guard against a double
send, and it has to match what the admin frontend computes character for
character (ahavah-admin/src/lib/growth-api.ts:565)."""
from datetime import datetime, timezone

from service.campaigns.weekid import week_campaign_id


def _at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_it_pads_the_week_to_two_digits():
    assert week_campaign_id(_at(2026, 1, 8), 'e2') == 'cmp_2026w02_comm'


def test_it_uses_the_iso_week_numbering_year_not_the_calendar_year():
    # 1 January 2027 falls in ISO week 53 of 2026.
    assert week_campaign_id(_at(2027, 1, 1), 'e2') == 'cmp_2026w53_comm'


def test_the_whole_monday_to_sunday_week_gets_one_id():
    ids = {week_campaign_id(_at(2026, 9, 21 + n), 'e2') for n in range(7)}
    assert ids == {'cmp_2026w39_comm'}


def test_each_campaign_has_its_own_suffix():
    assert week_campaign_id(_at(2026, 9, 21), 'e1') == 'cmp_2026w39_spotlight'
    assert week_campaign_id(_at(2026, 9, 21), 'e3') == 'cmp_2026w39_reinv'


def test_an_unknown_campaign_falls_back_to_its_own_code():
    assert week_campaign_id(_at(2026, 9, 21), 'e9') == 'cmp_2026w39_e9'
