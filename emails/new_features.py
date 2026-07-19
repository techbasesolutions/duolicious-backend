"""New-features announcement (2026-07 product update).

Campaign to the full list: the three member-facing upgrades that just
shipped — the You liked tab, the map lens ("Use my map view"), and the
discovery fixes that stop distance or unanswered profile fields from
hiding anyone. Canonical campaign template (Ultra title image, lime
CTA, unsubscribe footer).

Copy rule: NO em dashes anywhere (customer-facing).
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

SUBJECT = "Three upgrades just landed on Ahavah"
PREHEADER = "See everyone you liked, point discovery at your map, and meet more of the community."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"


def _feature(title: str, body: str) -> str:
    return f"""
<h3 style="margin:24px 0 6px;font-family:{SANS};font-size:15px;line-height:1.3;color:{INK};font-weight:800;">
  {title}
</h3>
<p class="e-text" style="margin:0;font-family:{SANS};font-size:16px;line-height:1.55;color:{INK_SOFT};">
  {body}
</p>
"""


def _body() -> str:
    return f"""
{chip("Product update")}

{title_image("title-new-features.png", "title-new-features-wht.png", "New features, live now.", 520)}

<p class="e-text" style="margin:0 0 4px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Your feedback has been shaping Ahavah all week. Three upgrades are
  live for every member today.
</p>

{_feature(
    "See everyone you liked",
    "Matches has a new tab: <strong style='color:" + INK + ";font-weight:700;'>You liked</strong>. "
    "Everyone you liked or super liked, waiting for a like back. "
    "Changed your mind? Open the small menu on a card and take the like "
    "back. They return to your Discover deck, no harm done."
)}

{_feature(
    "Point discovery at your map",
    "Turn on <strong style='color:" + INK + ";font-weight:700;'>Use my map view</strong> in the "
    "Discover filters. Browse the map to any part of the world, and the "
    "people in the area you last looked at appear first in your deck. "
    "Turn it off any time with one tap."
)}

{_feature(
    "Nobody stays hidden anymore",
    "We fixed how discovery treats distance and unanswered profile "
    "questions. If you prefer local matches you will see nearby people "
    "first, with the wider community right after. And leaving a profile "
    "question blank no longer hides you from anyone."
)}

{callout(
    "<strong>Seeing an empty feed before?</strong> That is fixed. Close "
    "and reopen the app once and your Discover feed and map will be "
    "full again."
)}

<p class="e-text" style="margin:24px 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Every one of these came from a member telling us what felt off. Keep
  it coming: just reply to this email.
</p>

{button("Open Ahavah &rarr;", SITE, variant="lime", full=True)}
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You are receiving this because you joined the Ahavah community.
<div style="margin-top:14px;">
  <a href="{SITE}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SITE}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""


def new_features_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
