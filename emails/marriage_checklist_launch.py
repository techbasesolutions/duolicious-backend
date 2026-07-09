"""Marriage checklist announcement email.

One-off campaign to the list (waitlist + members): the Marriage Checklist
is live. Faithful to the canonical campaign template (emails/reengagement.py,
the E4 structure): chip, Ultra display title rendered as an IMAGE (so the
typeface shows in clients that do not load web fonts), lede, lime CTA,
canonical footer with an unsubscribe line.

Copy rule: NO em dashes anywhere (customer-facing).
"""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    title_image,
    is_suppressed_send,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "The Marriage Checklist is here. Send it to someone."
PREHEADER = "Work through Scripture, decide what matters to you, and share it. Answers never stored."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"


def _who(label: str, text: str) -> str:
    return f"""
<p class="e-text" style="margin:0 0 14px;font-family:{SANS};font-size:16px;line-height:1.55;color:{INK_SOFT};">
  <strong class="e-strong" style="color:{INK};font-weight:700;">{label}</strong> {text}
</p>"""


def _body() -> str:
    return f"""
{chip("New free resource")}

{title_image("title-marriage-checklist.png", "title-marriage-checklist-wht.png", "The Marriage Checklist.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We just released something we think you will love. The Marriage Checklist is
  a free, guided activity: read the passages Scripture sets for a husband and
  a wife, decide in your own words what each one means to you, rate what
  matters most, and add your own nice-to-haves and challenges.
</p>
<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  At the end, your personal summary is emailed to you and whoever you choose.
  We never store your answers.
</p>

{_who("Married?", "Work through it together and compare summaries over dinner.")}
{_who("Courting, or talking to someone you are serious about?", "Send them your summary, or better, send them the checklist. There are few clearer ways to say you are intentional than asking someone where they stand on Scripture and marriage.")}
{_who("Single and clarifying what you want?", "Complete it for yourself. You will walk away knowing your own non-negotiables.")}

<div style="line-height:14px;height:14px;font-size:0;">&nbsp;</div>

{button("Take the checklist &rarr;", f"{SITE}/marriage-checklist", variant="lime", full=True)}

<p class="e-text" style="margin:18px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};text-align:center;">
  Know someone seeking a Torah-observant spouse? Pass this along.
</p>
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You are receiving this because you signed up at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{SITE}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SITE}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""


def launch_checklist_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
