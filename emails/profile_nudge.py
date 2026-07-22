"""Personalised profile-completion nudge.

Covers BOTH outstanding gaps in one message per member, so nobody gets
two emails on the same day:

  city     -> they picked a country but never a city, so the map cannot
              place them and they are invisible to anyone browsing it
  intent   -> "what you are looking for" is unset, so they are skipped
              whenever someone filters on it
  children -> children preference unset

Each member only sees the lines that apply to them. Members with no
gaps are never emailed (the runner filters them out).

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

SUBJECT = "One quick thing on your profile"
PREHEADER = "A couple of minutes now makes you easier to find."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"

# gap key -> (heading, explanation). The explanation says what it COSTS
# them, which is the honest reason to bother.
GAP_COPY = {
    "city": (
        "Add your city",
        "You picked a country but not a city, so you do not appear on "
        "the map yet. Members browsing the map cannot find you.",
    ),
    "intent": (
        "Say what you are looking for",
        "This one is used when people filter, so leaving it blank means "
        "you get skipped by members who would otherwise see you.",
    ),
    "children": (
        "Add your children preference",
        "It is one of the first things people check for compatibility.",
    ),
}


def _gap_block(key: str) -> str:
    heading, body = GAP_COPY[key]
    return f"""
<h3 style="margin:22px 0 6px;font-family:{SANS};font-size:15px;line-height:1.3;color:{INK};font-weight:800;">
  {heading}
</h3>
<p class="e-text" style="margin:0;font-family:{SANS};font-size:16px;line-height:1.55;color:{INK_SOFT};">
  {body}
</p>
"""


def _body(gaps: list[str]) -> str:
    blocks = "".join(_gap_block(g) for g in gaps if g in GAP_COPY)
    return f"""
{chip("Your profile")}

{title_image("title-finish-profile.png", "title-finish-profile-wht.png", "Finish your profile.", 500)}

<p class="e-text" style="margin:0 0 4px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Your profile is live, and there {"is one thing" if len(gaps) == 1 else "are a few things"} still missing:
</p>

{blocks}

{callout(
    "Open your profile, tap the box that says what is missing, and it "
    "takes you straight to the right field."
)}

<p class="e-text" style="margin:22px 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  It takes about two minutes. If anything gives you trouble, reply to
  this email and a person will help.
</p>

{button("Finish your profile &rarr;", f"{SITE}/profile/edit", variant="lime", full=True)}
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


def profile_nudge_html(email: str, gaps: list[str]) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(gaps),
        footer_html=_footer(email),
    )
