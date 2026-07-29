"""Likes-waiting nudge: members with unanswered likes who have been
away for days.

Likes deliberately never email in real time (email_likes defaults off,
approved notification matrix), so a member who drifts away can sit on
a pile of likes without knowing. This one-off tells them how many are
waiting, not who, and never notifies the likers of anything.

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

PREHEADER = "They are waiting to hear back."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"

_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six",
          7: "Seven", 8: "Eight", 9: "Nine"}


def subject_for(count: int) -> str:
    if count == 1:
        return "Someone likes you on Ahavah"
    return f"{count} members have liked you on Ahavah"


def likes_waiting_html(count: int, unsubscribe_url: str) -> str:
    if count == 1:
        lede = (
            "A member liked your profile on Ahavah and is waiting to hear "
            "back."
        )
    else:
        word = _WORDS.get(count, str(count))
        lede = (
            f"{word} members have liked your profile on Ahavah, and they "
            "are waiting to hear back."
        )
    body = f"""
{chip("New likes")}

{title_image("title-like.png", "title-like-wht.png", "Someone likes you.", 430)}

<p class="e-text" style="margin:0 0 18px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {lede}
</p>

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  A like only becomes a conversation when you answer it. Take a look
  and decide for yourself.
</p>

{button("See who likes you", f"{WEB_BASE_URL}/matches")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Only you can see your likes. No one is told when you look.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because members liked your Ahavah profile.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=subject_for(count),
        preheader=PREHEADER,
        body_html=body,
        footer_html=footer,
    )
