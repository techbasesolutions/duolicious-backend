"""Re-engagement REMINDER (second nudge) for waitlist signups who still have
no demographic answers after the first re-engagement email.

Visually identical to emails/reengagement.py — same brand shell, same indigo
chip, same Ultra outline title image (`title-reengage.png` / `-wht.png`), same
lime CTA, same footer. The body copy is softer and explicitly framed as a
last quiet nudge; the subject differentiates in the inbox so a recipient who
ignored the first one sees this as new, not a duplicate.

Audience: waitlist signups whose `answers` is still NULL / empty. The CTA
deep-links into /waitlist?email=<theirs> so the wizard recognises them and
walks the demographic steps instead of short-circuiting on the "already in"
interstitial.
"""
from __future__ import annotations

from urllib.parse import quote

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    chip,
    title_image,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SUBJECT = "A minute when you have one?"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "A few quick answers and we can have matches lined up for you the day we open."
SITE = "https://ahavah.app"


def _body(email: str) -> str:
    link = f"{SITE}/waitlist?email={quote(email)}"
    return f"""
{chip("One last nudge")}

{title_image("title-reengage.png", "title-reengage-wht.png", "Help us prepare your matches.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Shalom. We are sending one last quiet nudge before launch. You are on the
  Ahavah waitlist, but we do not yet have your answers about who you are and
  what you are looking for. When founding-member access opens this summer, we
  will not have matches ready for you on day one.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  It takes about a minute, every question is optional, and your spot stays
  exactly where it is whether you fill it in or not.
</p>

{button("Answer a few quick questions &rarr;", link, variant="lime", full=True)}

<p class="e-text" style="margin:18px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};">
  The link above opens the questions pre-filled to your email so the wizard
  recognises you and skips straight to the demographic steps. If now is not the
  moment, no problem at all. We will still email your sign-in link when we
  open.
</p>
"""


def _footer(email: str) -> str:
    unsub = f"mailto:admin@ahavah.app?subject={quote('Unsubscribe ' + email)}"
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah. Torah-observant matchmaking for the diaspora.<br/>
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


def reengagement_reminder_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(email),
        footer_html=_footer(email),
    )


def send_reengagement_reminder(email: str) -> None:
    """Synchronous best-effort send. Skips sample addresses."""
    if not email or email.endswith("@example.com"):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject=SUBJECT,
        body=reengagement_reminder_html(email),
        to_addr=email,
        from_addr=FROM_ADDR,
    )
