"""The campaign id for a given week.

`ahavah-admin/src/lib/growth-api.ts:565` computes this same string when an
operator presses Send. The cron computes it here. They MUST agree: the
runner's `_Q_SAME_RUN` dedupes on (campaign, campaign_id), so an identical
id is what makes a manual send and a scheduled send in the same week land
as one send rather than two. Change one side and you must change both.
"""
from __future__ import annotations

from datetime import datetime

_SUFFIX = {'e1': 'spotlight', 'e2': 'community', 'e3': 'reinvite'}


def week_campaign_id(now: datetime, campaign: str) -> str:
    # isocalendar() gives the ISO week-numbering year, which is not always
    # the calendar year: 1 January 2027 is 2026 week 53.
    year, week, _ = now.isocalendar()
    return f"cmp_{year}w{week:02d}_{_SUFFIX.get(campaign, campaign)}"
