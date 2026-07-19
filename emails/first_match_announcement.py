"""First-match milestone announcement.

One-off campaign to the list: Ahavah has its first match. Member details
stay private; the email celebrates the milestone and invites people in.
Faithful to the canonical campaign template (emails/reengagement.py /
emails/marriage_checklist_launch.py): chip, Ultra display title rendered
as an IMAGE, lede, lime CTA, canonical footer with an unsubscribe line.

Copy rule: NO em dashes anywhere (customer-facing).
"""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    title_image,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "It happened. Ahavah has its first match."
PREHEADER = "Two believers said yes to each other this week. Your person might be next."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"


def _body() -> str:
    return f"""
{chip("Milestone")}

{title_image("title-first-match.png", "title-first-match-wht.png", "The first match.", 480)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  This week, two Ahavah members liked each other and matched. The first
  match in Ahavah's story.
</p>
<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We are keeping their details private, as we always will. But we could not
  keep the news to ourselves: two Torah-observant believers, seeking
  marriage with intention, found each other here.
</p>
<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Every profile on Ahavah belongs to a believer who walks in Torah and
  wants marriage. The more of us there are, the faster stories like this
  one multiply. Yours might be next.
</p>

{button("Open Ahavah &rarr;", SITE, variant="lime", full=True)}

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


def first_match_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
