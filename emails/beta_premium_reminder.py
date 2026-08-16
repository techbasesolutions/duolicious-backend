"""Beta premium reminder.

One-off campaign to ACTIVATED MEMBERS ONLY (not the waitlist): every
member has Premium free for six months plus starter tokens, and during
the beta the checkout runs on Stripe's TEST gateway, so they can top up
tokens and try paid flows with the test card at no real cost.
Canonical campaign template (Ultra title image, lime CTA, unsubscribe
footer).

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
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "Your Premium is live. So are your free tokens."
PREHEADER = "Six months of Ahavah Premium, on us, plus tokens to spend. Top up free during the beta."
FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SITE = "https://ahavah.app"


def _body() -> str:
    return f"""
{chip("Beta perks")}

{title_image("title-premium-beta.png", "title-premium-beta-wht.png", "Premium is on us.", 500)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  As one of Ahavah's founding members, your account already has
  <strong class="e-strong" style="color:{INK};font-weight:700;">Premium free for six months</strong>:
  see who likes you, advanced filters, privacy controls like hiding your
  profile from strangers, and more.
</p>
<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  You also have <strong class="e-strong" style="color:{INK};font-weight:700;">free tokens</strong>
  waiting on your account for super likes, boosts, rewinds, and reveals.
  Check your balance under Profile, then spend them freely.
</p>

{callout(
    "<strong>Topping up is free during the beta.</strong> Payments run on a "
    "test gateway right now, so no real money can be charged. To add more "
    "tokens or try the Premium checkout, use the test card number "
    "<strong>4242 4242 4242 4242</strong> with any future expiry date and "
    "any 3 digit code. It works like a real purchase, but nothing is billed."
)}

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  This is the best time to try everything: your feedback on the paid
  features shapes what they become. If anything feels off, just reply to
  this email and tell us.
</p>

{button("Open Ahavah &rarr;", SITE, variant="lime", full=True)}
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


def beta_premium_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(),
        footer_html=_footer(email),
    )
