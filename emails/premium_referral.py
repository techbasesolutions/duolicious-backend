"""Premium + referral emails (2026-08-09 owner decision).

Two variants sharing one referral pitch:

  welcome  - members who were outside the old founding gate and just
             received their 6 months of Premium via the backfill.
  reminder - members already on Premium: their personal countdown date
             plus the referral pitch.

Facts the copy relies on (tested in tests/test_referral_rewards.py):
  - every new member gets 6 months of Premium free
  - a successful referral gives the inviter 30 days of Premium + 5 tokens
  - every member has a personal invite link ahavah.app/i/<code>

Copy rules: NO em dashes. Sentence case. Not wordy.
"""
from __future__ import annotations

from datetime import datetime

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    title_image,
    callout,
    INK_SOFT,
    MUTED,
    SANS,
)

FROM_ADDR = f"support@{EMAIL_DOMAIN}"

SUBJECT_WELCOME = "You have 6 months of Premium, free"
SUBJECT_REMINDER = "Every friend you bring adds a month of Premium"


def _fmt_date(dt: datetime) -> str:
    # "December 16, 2026" (no leading zero on the day)
    return dt.strftime("%B %d, %Y").replace(" 0", " ")


def _referral_block(invite_url: str) -> str:
    return f"""
<p class="e-text" style="margin:0 0 12px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Know someone who belongs here? Share your personal invite link:
</p>

<p style="margin:0 0 16px;font-family:{SANS};font-size:16px;line-height:1.5;">
  <a href="{invite_url}" style="color:#5524F5;font-weight:700;text-decoration:underline;word-break:break-all;">{invite_url.replace("https://", "")}</a>
</p>

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  They get their own 6 months of Premium free. And every friend who
  joins and completes their profile adds 30 days of Premium and 5
  tokens to your account.
</p>
"""


def welcome_html(
    premium_until: datetime,
    invite_url: str,
    unsubscribe_url: str,
) -> str:
    body = f"""
{chip("Premium")}

{title_image("title-premium-welcome.png", "title-premium-welcome-wht.png", "Six months, on us.", 430)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Every early member of Ahavah gets 6 months of Premium free, and that
  includes you. Your account has been upgraded: Premium runs until
  <strong>{_fmt_date(premium_until)}</strong>, and 30 tokens are in
  your wallet for super likes, boosts and more.
</p>

{_referral_block(invite_url)}

{button("Open Ahavah", f"{WEB_BASE_URL}/discover")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Premium and tokens are already on your account. Nothing to claim, nothing to enter.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=SUBJECT_WELCOME,
        preheader="Your Premium is live, with 30 tokens to spend.",
        body_html=body,
        footer_html=footer,
    )


def reminder_html(
    premium_until: datetime,
    invite_url: str,
    unsubscribe_url: str,
) -> str:
    body = f"""
{chip("Premium")}

{title_image("title-premium-reminder.png", "title-premium-reminder-wht.png", "Every friend adds a month.", 486)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Your free early-member Premium runs until
  <strong>{_fmt_date(premium_until)}</strong>. Want more time? Bring
  the people who should be here anyway.
</p>

{_referral_block(invite_url)}

{button("Open Ahavah", f"{WEB_BASE_URL}/discover")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Your invite link never expires and there is no limit on how many friends can extend your Premium.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=SUBJECT_REMINDER,
        preheader=f"Premium until {_fmt_date(premium_until)}. Referrals extend it.",
        body_html=body,
        footer_html=footer,
    )
