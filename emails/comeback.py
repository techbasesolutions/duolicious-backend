"""Come-back invitation for dormancy-deactivated members.

The four members this targets lapsed before the dormancy email was
rebranded, so the last thing Ahavah sent them was the off-brand
upstream template. This is the deliberate, on-brand invitation:
nothing was deleted, the community has kept growing, one sign-in
restores everything.

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

SUBJECT = "Your place on Ahavah is still yours"
PREHEADER = "Nothing was deleted. One sign-in brings it all back."
FROM_ADDR = f"support@{EMAIL_DOMAIN}"


def comeback_html(unsubscribe_url: str) -> str:
    body = f"""
{chip("Come back")}

{title_image("title-comeback.png", "title-comeback-wht.png", "We kept your place.", 447)}

<p class="e-text" style="margin:0 0 18px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Your Ahavah profile has been resting while you were away. Nothing
  was deleted: your profile, your likes, and your conversations are
  exactly where you left them.
</p>

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  The community has kept growing since you left. New members join
  every week, and the first couples are already talking.
</p>

{button("Return to Ahavah", f"{WEB_BASE_URL}/")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("One sign-in restores your profile instantly.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because your Ahavah profile was hidden after a
period of inactivity.
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
