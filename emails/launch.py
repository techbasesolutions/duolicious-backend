"""Launch email -- one-shot blast to every waitlist registrant.

Each recipient gets a personal claim link (ahavah.app/claim/<token>) that logs
them straight in and pre-fills their account from their waitlist answers. Tone:
celebratory but honest about early-days bugs, with an explicit feedback ask and
an invite-your-friends line. Inline-styled via emails.base; dark-mode +
suppression + unsubscribe handled by the family conventions. Send is gated by
emails.send_launch (CLI, dry-run by default; not fired by any route).
"""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    callout,
    title_image,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "Ahavah is live. Come on in."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "Your personal link logs you straight in. Help us begin."
SITE = "https://ahavah.app"


def _body(claim_url: str) -> str:
    return f"""
{chip("We're live")}

{title_image("title-launch.png", "title-launch-wht.png", "We're live.", 360)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Ahavah is open. You signed up early, before there was anything to
  show you, and you trusted us with that. Thank you. Today the door
  opens for you.
</p>
<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  A word of honesty first: these are early days. You will find rough
  edges, and the occasional thing that breaks. That is normal for
  something brand new, and it is exactly where you come in.
</p>

{callout("When something feels off or breaks, please tell us. Reply to this email, or tap Report inside the app. Every note genuinely shapes what Ahavah becomes.")}

<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Your personal link below logs you straight in and brings over the
  answers you already gave us. You will just finish your profile (a few
  details and your photos) and you are in.
</p>

{button("Open Ahavah &rarr;", claim_url, variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};text-align:center;">
  Or copy and paste this link:
</p>
<p class="e-text" style="margin:4px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{INDIGO};text-align:center;word-break:break-all;">
  <a href="{claim_url}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{claim_url}</a>
</p>

<p class="e-text" style="margin:24px 0 0;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  Know someone Torah-observant who belongs here? Bring them. Your invite
  link lives in your profile once you are inside, and it is the best way
  to help this community grow with the right people.
</p>
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you joined the waitlist at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
</div>
"""


def launch_html(email: str, claim_url: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(claim_url),
        footer_html=_footer(email),
    )
