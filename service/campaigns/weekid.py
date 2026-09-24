"""The campaign id for a given week.

`ahavah-admin/src/lib/growth-api.ts:565` computes this same string when an
operator presses Send. The cron computes it here. They MUST agree: the
runner's `_Q_SAME_RUN` dedupes on (campaign, campaign_id), so an identical
id is what makes a manual send and a scheduled send in the same week land
as one send rather than two. Change one side and you must change both.
"""
from __future__ import annotations

from datetime import datetime

# Copied from CAMPAIGN_SUFFIX in ahavah-admin/src/lib/growth-api.ts. The
# admin values are the established ones, because operators have been pressing
# Send against them since before any of this was scheduled. Do not "tidy"
# them into longer words: `comm` and `reinv` are what is already in
# email_send_log, and they are pinned on both sides (see the cross-repo test
# in ahavah-admin/tests/growth-api.test.mjs).
_SUFFIX = {'e1': 'spotlight', 'e2': 'comm', 'e3': 'reinv'}


def week_campaign_id(now: datetime, campaign: str) -> str:
    # isocalendar() gives the ISO week-numbering year, which is not always
    # the calendar year: 1 January 2027 is 2026 week 53.
    year, week, _ = now.isocalendar()
    return f"cmp_{year}w{week:02d}_{_SUFFIX.get(campaign, campaign)}"
