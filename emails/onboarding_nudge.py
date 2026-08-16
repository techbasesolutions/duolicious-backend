"""Onboarding-completion nudge: members who created an account but
never finished onboarding, so nobody can see them yet.

Distinct from profile_nudge (activated members with field gaps): this
targets NOT-activated members whose sign-up is recent. First recipient
class: Charles, who fought through an IP block to sign up and then
stalled mid-onboarding.

Copy rules: NO em dashes. Sentence case.
"""
from __future__ import annotations

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

SUBJECT = "You're two minutes from joining Ahavah"
PREHEADER = "Your account is created. Finish your profile to be seen."
FROM_ADDR = f"support@{EMAIL_DOMAIN}"


def onboarding_nudge_html(name: str | None, unsubscribe_url: str) -> str:
    greeting = f"{name}, your" if name else "Your"
    body = f"""
{chip("Almost there")}

{title_image("title-finish-profile.png", "title-finish-profile-wht.png", "Finish your profile.", 447)}

<p class="e-text" style="margin:0 0 18px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {greeting} Ahavah account is created and waiting. A few steps remain
  before the community can see you.
</p>

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Pick up where you left off, add a photo and a little about yourself,
  and you're in.
</p>

{button("Finish my profile", f"{WEB_BASE_URL}/")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("It takes about two minutes.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you started creating an Ahavah profile.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=body,
        footer_html=footer,
    )
