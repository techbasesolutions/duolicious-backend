"""Chat-bug fix notice.

A location-inherited verification hold was silently rejecting outgoing
messages from affected members, including inside confirmed matches. Found
and fixed 2026-07-21. This tells members plainly, and tells anyone whose
message failed to simply send it again.

Canonical campaign template (Ultra title image, lime CTA, unsubscribe
footer).

Copy rules: NO em dashes. Sentence case. No mirrored-clause phrasing.
"""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    title_image,
    callout,
    INK,
    INK_SOFT,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "We found a chat bug. It is fixed."
PREHEADER = "If a message of yours would not send, please try it again."
FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"


def _body() -> str:
    return f"""
{chip("Fixed")}

{title_image("title-chat-fixed.png", "title-chat-fixed-wht.png", "We fixed a chat bug.", 500)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Some messages were being blocked from sending, and the app did not
  say why. It could affect people who had already matched, which is
  the worst possible time for it to happen.
</p>

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We found it and fixed it today. If you have matched with someone,
  you can always message each other.
</p>

{callout(
    "<strong>Did a message of yours fail to send?</strong> Open that "
    "chat and send it again. It will go through now."
)}

<p class="e-text" style="margin:24px 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Sorry to anyone this cost a conversation. If something still feels
  wrong, reply to this email and a person will read it.
</p>

{button("Open your messages &rarr;", SITE, variant="lime", full=True)}
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You are receiving this because you are an Ahavah member.
<div style="margin-top:14px;">
  <a href="{SITE}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SITE}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""


def chat_fix_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
