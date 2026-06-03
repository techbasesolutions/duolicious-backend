"""Re-engagement / demographic-capture email for waitlist signups.

Faithful port of the canonical E4 "re-engagement" template
(emails/canonical/Ahavah-Email-Templates.html): light card, white-logo header,
indigo kicker pill, Ultra display title rendered as an IMAGE (so the typeface
shows in clients that do not load web fonts), lede, lime CTA, canonical footer
with an unsubscribe line.

Audience: waitlist signups who gave only an email (no demographic answers yet).
The CTA deep-links into /waitlist?email=<theirs> so the wizard recognises them
and walks the demographic steps instead of short-circuiting.
"""
from __future__ import annotations

from urllib.parse import quote

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    chip,
    title_image,
    is_suppressed_send,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SUBJECT = "Help us prepare your matches"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "A minute of quick questions so we can line up good matches for you before launch."
SITE = "https://ahavah.app"


def _body(email: str) -> str:
    link = f"{SITE}/waitlist?email={quote(email)}"
    return f"""
{chip("Before launch")}

{title_image("title-reengage.png", "title-reengage-wht.png", "Help us prepare your matches.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Thank you for joining the Ahavah waitlist. We are building Torah-observant
  matchmaking for Messianic, Hebrew Roots, and Torah-observant believers who are
  seeking a spouse.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  To line up good matches for you before launch, we would love a few quick
  answers: who you are, where you live, and what you are looking for. It takes
  about a minute, and every question is optional.
</p>

{button("Answer a few questions &rarr;", link, variant="lime", full=True)}

<p class="e-text" style="margin:18px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};">
  Your sign-in link is still on the way. We will email it the moment we open
  founding-member access this summer. These answers just help us prepare better
  matches for you. You can ignore this and your spot on the waitlist stays
  exactly as it is.
</p>
"""


def _footer(email: str) -> str:
    unsub = f"mailto:admin@ahavah.app?subject={quote('Unsubscribe ' + email)}"
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You are receiving this because you joined the waitlist at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{SITE}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SITE}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""


def reengagement_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(email),
        footer_html=_footer(email),
    )


def send_reengagement(email: str) -> None:
    """Synchronous best-effort send. Skips sample addresses."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject=SUBJECT,
        body=reengagement_html(email),
        to_addr=email,
        from_addr=FROM_ADDR,
    )
