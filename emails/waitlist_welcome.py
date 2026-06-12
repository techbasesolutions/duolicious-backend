"""Waitlist welcome email (growth / share focus).

Faithful port of the canonical E1 "Welcome" template
(emails/canonical/Ahavah-Email-Templates.html): light card, white logo header,
indigo chip, Ultra title with one indigo accent word, lime callout, lime CTA,
canonical footer. Audience: landing-page email signups (no account yet, so no
sign-in code). Inline-styled + dark-mode aware via emails.base.
"""
from __future__ import annotations

import threading
import traceback

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    callout,
    title_image,
    is_suppressed_send,
    INK,
    INK_SOFT,
    INDIGO,
    LIME,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "You're on the Ahavah waitlist"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SHARE_URL = "https://ahavah.app"
PREHEADER = "You're on the list. Ahavah launches Summer 2026. Founding members get six months of Premium free."

_BODY = f"""
{chip("You're on the list")}

{title_image("title-welcome.png", "title-welcome-wht.png", "Welcome to Ahavah.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Thank you for joining. Ahavah is Torah-observant matchmaking for serious
  believers, here to help you find a spouse aligned in Torah, faith, family,
  and covenant.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We launch <strong class="e-strong" style="color:{INK};font-weight:700;">Summer 2026</strong>.
  We'll email your sign-in link the moment invites open.
</p>

{callout("Founding member perk: six months of Premium free at launch.")}

<hr style="height:1px;background:rgba(15,11,31,0.08);border:none;margin:0 0 28px;"/>

{title_image("subhead-welcome.png", "subhead-welcome-wht.png", "Know someone else seeking marriage?", 528)}
<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  Ahavah grows by word of mouth. Share it with believers who are serious about
  marriage, family, and building a Torah-based home.
</p>

{button("Invite a friend &rarr;", SHARE_URL, variant="lime", full=True)}

<p class="e-text" style="margin:16px 0 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{MUTED};text-align:center;">
  Or simply forward this email.
</p>
"""

def _footer(email: str) -> str:
    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you joined the waitlist at
<a href="{SHARE_URL}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{SHARE_URL}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SHARE_URL}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SHARE_URL}/legal/terms" style="{link_style}">Terms</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""


def waitlist_welcome_html(email: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_BODY,
        footer_html=_footer(email),
    )


def send_waitlist_welcome(email: str) -> None:
    """Synchronous send. Skips sample/suppressed addresses. Best-effort
    (aws_smtp retries then gives up without raising). Stamps the
    waitlist_signup row with the send time + Resend message id so the
    otherwise-untraceable fire-and-forget welcome is auditable."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp

    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    message_id = aws_smtp.send(
        subject=SUBJECT,
        body=waitlist_welcome_html(email),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=(
            f"<mailto:admin@ahavah.app?subject=Unsubscribe>, <{unsub}>"
        ),
    )

    # Auditability: record that the welcome went out + the Resend id (for
    # per-recipient delivery lookups in the Resend dashboard). Best-effort —
    # the email is already sent, so a logging-write failure must not raise.
    try:
        from database import api_tx

        with api_tx() as tx:
            tx.execute(
                "UPDATE waitlist_signup "
                "SET welcome_sent_at = NOW(), welcome_message_id = %(mid)s "
                "WHERE lower(email) = lower(%(email)s)",
                dict(mid=message_id, email=email),
            )
    except Exception:
        print(traceback.format_exc())


def send_waitlist_welcome_async(email: str) -> None:
    """Fire-and-forget so the signup request returns immediately. Failures are
    swallowed (the welcome is non-critical; the row is already saved)."""
    if is_suppressed_send(email):
        return

    def _go() -> None:
        try:
            send_waitlist_welcome(email)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
