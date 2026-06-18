"""Community-invite email — one-shot blast to everyone who entered an email.

Invites recipients to the Ahavah community Facebook group. No referral code or
claim link: the CTA is the fixed group URL, the same for every recipient. Same
template family as emails/referral_community.py (chip + text heading so no new
rendered title asset is needed); dark-mode + suppression + unsubscribe handled
by the family conventions. Send is gated by emails/send_community_invite.py,
not fired by any route."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "There's a place for you in the Ahavah community"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "More than matchmaking. A community of believers walking the same path. Come say shalom."
SITE = "https://ahavah.app"
GROUP_URL = "https://www.facebook.com/share/g/1DHBNAS4Gw/"


def _body() -> str:
    return f"""
{chip("Community")}

<h1 style="margin:0 0 18px;font-family:{SANS};font-size:30px;line-height:1.15;font-weight:800;color:{INK};">
  There&rsquo;s a place for you in our community.
</h1>

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Ahavah has always been about more than matchmaking. At its heart it is a
  community of Messianic, Torah-observant believers who share the same walk and
  want to encourage one another along the way.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  So we have opened a space for exactly that. Our community group on Facebook is
  where members, friends, and fellow seekers come together to connect, share,
  and build real relationships beyond the app. Whether you are already inside
  Ahavah, still on the waitlist, or simply curious, you are welcome here.
</p>

{button("Join the Ahavah community &rarr;", GROUP_URL, variant="lime", full=True)}

<p class="e-text" style="margin:18px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};">
  Come introduce yourself and say shalom. We would love to have you with us.
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


def community_invite_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
