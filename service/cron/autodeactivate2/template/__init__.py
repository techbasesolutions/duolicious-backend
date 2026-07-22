"""Dormancy deactivation email.

Brand-consistent rebuild on emails.base (the canonical shell every other
Ahavah email uses), replacing the un-rebranded upstream Duolicious template
("Baby, come back!", #70f on lavender) that survived the debrand because
this cron fires rarely and unreviewed.

Voice note: this lands after 30 quiet days. The member owes us nothing, so
the tone is a held place, not a guilt trip: nothing is lost, one sign-in
restores everything.
"""
from service.config import PRODUCT_NAME, WEB_BASE_URL
from emails.base import (
    render,
    chip,
    callout,
    title_image,
    button,
    INK_SOFT,
    MUTED,
    SANS,
)


def emailtemplate():
    body = f"""
{chip("Account update")}

{title_image("title-resting.png", "title-resting-wht.png", "Your profile is resting.", 516)}

<p class="e-text" style="margin:0 0 18px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We only show active members, so after a month away your profile has been
  set aside. Until you return, other members won't see you, and you won't
  receive new likes or messages.
</p>

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Nothing has been deleted. Your profile, your matches, and your
  conversations are all exactly where you left them.
</p>

{button(f"Return to {PRODUCT_NAME}", f"{WEB_BASE_URL}/")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("One sign-in is all it takes. Your profile is restored the moment you're back.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because your {PRODUCT_NAME} profile was hidden after 30 days of inactivity.
<div style="margin-top:14px;">
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/legal/terms" style="color:{MUTED};font-weight:600;text-decoration:underline;">Terms</a>
</div>
"""
    return render(
        title=f"Your {PRODUCT_NAME} profile is resting",
        preheader="Nothing is deleted. One sign-in brings everything back.",
        body_html=body,
        footer_html=footer,
    )
