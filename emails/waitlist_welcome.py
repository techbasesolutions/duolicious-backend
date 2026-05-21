"""Waitlist welcome email (growth / share focus).

Audience: people who gave their email on the landing page. No account yet, so
no sign-in link. Premium "founding member / remnant" treatment: a solid
dark-indigo hero (client-safe) + a share-led white body. Built table-based +
inline-styled per emails/README.md; visual source of truth is
emails/canonical/Ahavah-Email-Templates.html.
"""
from __future__ import annotations

import threading
import traceback

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    LOGO_WHITE_URL,
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
PREHEADER = "You're on the list. Ahavah launches Summer 2026 — founding members get six months of Premium free."

# Dark hero — solid background (email-safe; CSS gradients are unreliable in
# Outlook). bgcolor attribute + inline background for max client coverage.
_HERO = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#1A1340" style="background:#1A1340;">
  <tr><td align="center" style="padding:40px 36px 36px;text-align:center;">
    <img src="{LOGO_WHITE_URL}" alt="Ahavah" height="28" style="height:28px;width:auto;display:inline-block;border:0;outline:none;text-decoration:none;"/>
    <div style="margin:24px 0 0;">
      <span style="display:inline-block;padding:7px 16px;border-radius:999px;background:#241C52;color:{LIME};font-family:{SANS};font-size:12px;font-weight:bold;letter-spacing:0.16em;text-transform:uppercase;">&#9679;&nbsp; You're on the list</span>
    </div>
    <h1 style="margin:22px 0 12px;font-family:{SERIF};font-size:42px;line-height:1.02;letter-spacing:-0.02em;font-weight:normal;color:#ffffff;">
      Welcome to the <span style="color:{LIME};">remnant.</span>
    </h1>
    <p style="margin:0 auto;max-width:420px;font-family:{SANS};font-size:16px;line-height:1.5;color:#C9C4E0;">
      Torah-observant matchmaking for serious believers. You're early, and that is exactly the point.
    </p>
  </td></tr>
</table>
"""

_BODY = f"""
<p style="margin:0 0 22px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK_SOFT};">
  Thank you for joining. Ahavah is built for believers seriously seeking marriage,
  to find someone aligned in Torah, faith, family, and covenant. We launch
  <strong style="color:{INK};">Summer 2026</strong>, and we'll email your sign-in
  link the moment invites open.
</p>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 30px;">
  <tr>
    <td width="4" bgcolor="{LIME}" style="background:{LIME};border-radius:4px 0 0 4px;">&nbsp;</td>
    <td bgcolor="#F4FBE3" style="padding:14px 18px;background:#F4FBE3;border-radius:0 10px 10px 0;font-family:{SANS};font-size:14px;line-height:1.5;color:{INK};font-weight:bold;">
      Founding member perk &mdash; six months of Premium free at launch.
    </td>
  </tr>
</table>

<hr style="height:1px;background:rgba(15,11,31,0.08);border:none;margin:0 0 30px;"/>

<h2 style="margin:0 0 10px;font-family:{SERIF};font-size:26px;line-height:1.1;letter-spacing:-0.01em;font-weight:normal;color:{INK};">
  Know others seeking marriage?
</h2>
<p style="margin:0 0 24px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  Ahavah grows quietly, by word of mouth among the remnant. Share it with someone
  who is serious about marriage, family, and building a Torah-based home.
</p>

{button("Invite the remnant &rarr;", SHARE_URL, variant="lime")}

<p style="margin:16px 0 0;font-family:{SANS};font-size:13px;line-height:1.5;color:{MUTED};text-align:center;">
  Or simply forward this email to a friend.
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
        hero_html=_HERO,
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
