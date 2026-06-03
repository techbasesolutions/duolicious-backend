"""Beta launch announcement email.

Sent (via the emails/send_beta_launch.py CLI) to the beta_signup cohort when the
beta opens. Links to the existing sign-in page; sign-in is OTP-code based, so the
copy explains "enter your email, we'll send a one-time code". Inline-styled +
dark-mode aware via emails.base. Best-effort send (aws_smtp retries then gives up
without raising)."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    chip,
    callout,
    title_image,
    is_suppressed_send,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SIGN_IN_URL = "https://ahavah.app/auth/sign-in"
SUBJECT = "Ahavah beta is open"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "The Ahavah beta is open. Sign in with your email to get started."

_BODY = f"""
{chip("Beta is live")}

{title_image("title-beta-launch.png", "title-beta-launch-wht.png", "The beta is open.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  You signed up to test Ahavah, Torah-observant matchmaking for serious
  believers, and it's ready for you. Welcome in.
</p>

{callout("Sign in with the email you used to join. We'll send you a one-time code, no password.")}

{button("Open Ahavah &rarr;", SIGN_IN_URL, variant="lime", full=True)}

<p class="e-text" style="margin:16px 0 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{MUTED};text-align:center;">
  Or go to ahavah.app/auth/sign-in
</p>
"""

_FOOTER = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you joined the Ahavah beta at
<a href="https://ahavah.app" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
"""


def beta_launch_html() -> str:
    return render(title=SUBJECT, preheader=PREHEADER, body_html=_BODY, footer_html=_FOOTER)


def send_beta_launch(email: str) -> None:
    """Synchronous send. Skips sample addresses. Best-effort (aws_smtp retries
    then gives up without raising)."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject=SUBJECT,
        body=beta_launch_html(),
        to_addr=email,
        from_addr=FROM_ADDR,
    )
