"""Render smoke test for the evergreen member welcome.

The full send path (grant latch -> async send) is exercised implicitly
by test_referral_rewards.py (the latch) and by the live catch-up
runner; here we pin the personalized facts into the rendered HTML so a
refactor can't silently drop the date, the invite link, or the token
stipend from the copy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from emails.member_welcome import member_welcome_html, SUBJECT


def test_welcome_html_contains_personalized_facts():
    html = member_welcome_html(
        datetime(2027, 2, 9, tzinfo=timezone.utc),
        "https://ahavah.app/i/737TGEG",
        "https://ahavah.app/unsub-test",
    )
    assert "February 9, 2027" in html
    assert "https://ahavah.app/i/737TGEG" in html
    assert "ahavah.app/i/737TGEG" in html          # visible link text
    assert "30 tokens" in html
    assert "6 months" in html
    assert "https://ahavah.app/unsub-test" in html
    assert "title-member-welcome" in html
    # No em dashes anywhere in member-facing copy.
    assert "—" not in html


def test_subject_has_no_em_dash():
    assert "—" not in SUBJECT
