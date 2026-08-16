"""Referral-intro email — one-shot blast to the beta cohort.

Sends each current beta_signup their personal referral link plus the
reward framing. Inline-styled via emails.base; dark-mode + suppression
+ unsubscribe all handled by the family conventions. Send is gated by
emails.send_referral_intro CLI (not fired by any route)."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
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
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "Your link to bring someone in"
FROM_ADDR = f"support@{EMAIL_DOMAIN}"
PREHEADER = "You earn a Boost for each friend who joins through your link."
SITE = "https://ahavah.app"


def _body(code: str) -> str:
    share_url = f"{WEB_BASE_URL}/share/{code}"
    plaintext_url = f"{WEB_BASE_URL}/i/{code}"
    return f"""
{chip("You + 1")}

{title_image("title-referral.png?v=2", "title-referral-wht.png?v=2", "Bring someone with you.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Shalom. You were one of the first people to opt into the Ahavah beta.
  We are not running this loudly. We are building it one trusted person
  at a time. That is why we are writing to you specifically.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Here is your personal invite link. Send it to one Torah-observant
  friend you would want to see meet someone good. When they sign up
  through it, your name is on the door for them.
</p>

{callout("When that friend finishes their profile at launch, we credit your account with 5 tokens. That is one Boost: a 30-minute spotlight on Discover. Hold it for the right moment.")}

{button("Share your link &rarr;", share_url, variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};text-align:center;">
  Or copy and paste this link:
</p>
<p class="e-text" style="margin:4px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{INDIGO};text-align:center;word-break:break-all;">
  <a href="{plaintext_url}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{plaintext_url}</a>
</p>

<p class="e-text" style="margin:24px 0 0;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  As founding members and the app's earliest adopters, you play a
  critical role. You shape how Ahavah works, and whether it adds
  enough value to be successful.
</p>
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you opted into the Ahavah beta at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
</div>
"""


def referral_intro_html(email: str, code: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(code),
        footer_html=_footer(email),
    )


def send_referral_intro(email: str, code: str) -> None:
    """Synchronous send. Skips suppressed addresses (example.com /
    techbaseltd.com). Best-effort (aws_smtp retries then gives up
    without raising)."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    aws_smtp.send(
        subject=SUBJECT,
        body=referral_intro_html(email, code),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=(
            f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>"
        ),
    )
