"""Beta-tester confirmation email.

Sent when an onboarder opts in as a beta tester. Tells them a sign-in link is
coming on June 15, 2026 (the actual link is a separate future batch). Inline-
styled + dark-mode aware via emails.base. Best-effort send (aws_smtp retries
then gives up without raising)."""
from __future__ import annotations

import threading
import traceback

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    chip,
    callout,
    title_image,
    is_suppressed_send,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SIGN_IN_DATE = "June 15, 2026"
SUBJECT = "You're an Ahavah beta tester"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SHARE_URL = "https://ahavah.app"
PREHEADER = f"You're in. We'll email your sign-in link on {SIGN_IN_DATE}."

_BODY = f"""
{chip("Beta tester")}

{title_image("title-beta-welcome.png", "title-beta-welcome-wht.png", "You're in.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Thank you for signing up to test Ahavah. You're on the beta list, helping shape
  Torah-observant matchmaking before it opens to everyone.
</p>

{callout(f"Your sign-in link arrives by email on <strong>{SIGN_IN_DATE}</strong>. Watch your inbox.")}

<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  Nothing else to do for now. We'll be in touch.
</p>
"""

def _footer(email: str) -> str:
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you opted into the beta at
<a href="{SHARE_URL}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
</div>
"""


def beta_welcome_html(email: str) -> str:
    return render(title=SUBJECT, preheader=PREHEADER, body_html=_BODY, footer_html=_footer(email))


def send_beta_welcome(email: str) -> None:
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    aws_smtp.send(
        subject=SUBJECT,
        body=beta_welcome_html(email),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=(
            f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>"
        ),
    )


def send_beta_welcome_async(email: str) -> None:
    """Fire-and-forget so the request returns immediately; failures are swallowed
    (the beta_signup row is the source of truth)."""
    if is_suppressed_send(email):
        return

    def _go() -> None:
        try:
            send_beta_welcome(email)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
