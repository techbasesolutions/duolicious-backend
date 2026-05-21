"""Waitlist welcome email (growth / share focus).

Audience: people who gave their email on the landing page. No account yet, so
no sign-in link. Mirrors the canonical E1 "Welcome" template (light, logo
header, indigo chip, Ultra title with one indigo accent word, lime callout) in
emails/canonical/Ahavah-Email-Templates.html. Table-based + inline-styled per
emails/README.md.
"""
from __future__ import annotations

import threading
import traceback

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    chip,
    INK,
    INK_SOFT,
    INDIGO,
    LIME,
    MUTED,
    SERIF,
    SANS,
)

SUBJECT = "You're on the Ahavah waitlist"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
SHARE_URL = "https://ahavah.app"
PREHEADER = "You're on the list. Ahavah launches Summer 2026. Founding members get six months of Premium free."

_BODY = f"""
{chip("You're on the list")}

<h1 style="margin:18px 0 14px;font-family:{SERIF};font-size:42px;line-height:1.02;letter-spacing:-0.022em;font-weight:normal;color:{INK};">
  Welcome to <span style="color:{INDIGO};">Ahavah</span>.
</h1>

<p style="margin:0 0 18px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Thank you for joining. Ahavah is Torah-observant matchmaking for serious
  believers, here to help you find a spouse aligned in Torah, faith, family,
  and covenant.
</p>
<p style="margin:0 0 26px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  We launch <strong style="color:{INK};">Summer 2026</strong>. We'll email your
  sign-in link the moment invites open.
</p>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 30px;">
  <tr>
    <td width="4" bgcolor="{LIME}" style="background:{LIME};border-radius:4px 0 0 4px;">&nbsp;</td>
    <td bgcolor="#F4FBE3" style="padding:14px 18px;background:#F4FBE3;border-radius:0 10px 10px 0;font-family:{SANS};font-size:14px;line-height:1.5;color:{INK};font-weight:bold;">
      Founding member perk: six months of Premium free at launch.
    </td>
  </tr>
</table>

<hr style="height:1px;background:#E7E3D8;border:none;margin:0 0 30px;"/>

<h2 style="margin:0 0 10px;font-family:{SERIF};font-size:26px;line-height:1.1;letter-spacing:-0.01em;font-weight:normal;color:{INK};">
  Know someone else seeking marriage?
</h2>
<p style="margin:0 0 24px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  Ahavah grows by word of mouth. Share it with believers who are serious about
  marriage, family, and building a Torah-based home.
</p>

{button("Invite a friend &rarr;", SHARE_URL, variant="lime")}

<p style="margin:16px 0 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{MUTED};text-align:center;">
  Or simply forward this email.
</p>
"""

_FOOTER = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you joined the waitlist at
<a href="{SHARE_URL}" style="color:{INDIGO};font-weight:bold;text-decoration:none;">ahavah.app</a>.
"""


def waitlist_welcome_html() -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_BODY,
        footer_html=_FOOTER,
    )


def send_waitlist_welcome(email: str) -> None:
    """Synchronous send. Skips sample addresses. Best-effort (aws_smtp retries
    then gives up without raising)."""
    if not email or email.endswith("@example.com"):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject=SUBJECT,
        body=waitlist_welcome_html(),
        to_addr=email,
        from_addr=FROM_ADDR,
    )


def send_waitlist_welcome_async(email: str) -> None:
    """Fire-and-forget so the signup request returns immediately. Failures are
    swallowed (the welcome is non-critical; the row is already saved)."""
    if not email or email.endswith("@example.com"):
        return

    def _go() -> None:
        try:
            send_waitlist_welcome(email)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
