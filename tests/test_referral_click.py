"""Unit tests for service.api.referral_click_route.

Only the pure-logic piece (_classify_ua) is unit-tested here. The
DB-touching pieces (the POST /referral-click route body, the inviter
lookup, the insert) are integration-tested via the live smoke flow
documented in
docs/superpowers/handovers/2026-06-06-beta-referrals-handover.md §5.

The repo's convention is to keep the test surface narrow — mocking
psycopg cursors is brittle and obscures whether the actual SQL is
right. Live smoke against the live test stack catches more."""
from __future__ import annotations

import pytest

from service.api.referral_click_route import _classify_ua


@pytest.mark.parametrize("ua, expected", [
    # Empty/missing
    ("", "unknown"),
    (None, "unknown"),
    # Bots / preview crawlers
    ("facebookexternalhit/1.1", "bot"),
    ("Twitterbot/1.0", "bot"),
    ("LinkedInBot/1.0", "bot"),
    ("Slackbot-LinkExpanding 1.0", "bot"),
    ("Discordbot/2.0", "bot"),
    ("TelegramBot (like TwitterBot)", "bot"),
    ("WhatsApp/2.21.4.18", "bot"),
    ("Applebot/0.1", "bot"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1)", "bot"),
    ("Mozilla/5.0 (compatible; bingbot/2.0)", "bot"),
    ("Some Crawler v1.0", "bot"),
    ("WebSpider/1.0", "bot"),
    ("Mozilla/5.0 (LinkPreview)", "bot"),
    # Mobile (the iphone/android/mobile signals)
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "mobile"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8)", "mobile"),
    ("Mozilla/5.0 (Windows Phone 10.0; Mobile)", "mobile"),
    # Desktop fallback
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", "desktop"),
    ("Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0", "desktop"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/119.0", "desktop"),
])
def test_classify_ua(ua, expected):
    assert _classify_ua(ua) == expected


def test_classify_ua_bot_check_runs_before_mobile_check():
    """A UA that says BOTH 'mobile' and 'bot' classifies as bot.
    Ensures the bot keywords are checked first (more conservative
    for analytics — bot-tinged 'mobile' UAs come from preview
    crawlers and we don't want them inflating the mobile bucket)."""
    # iOS Slackbot variant in the wild
    assert _classify_ua("Mozilla/5.0 (iPhone) Slackbot 1.0") == "bot"
