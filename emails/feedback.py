"""Public feedback notification email (internal — to the Ahavah admin).

POST /feedback emails a clean, branded-lite summary to TO_ADDR. This is an
internal ops notification (not a user-facing brand email), so it deliberately
does NOT use the full canonical E1 shell (logo header / waitlist footer); it is
a simple, readable card. ALL user-supplied content is HTML-escaped to prevent
injection into the email body. Best-effort send (aws_smtp retries then gives up
without raising); the async helper swallows failures so the request is never
blocked or broken by SMTP.
"""
from __future__ import annotations

import html
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional

from service.config import EMAIL_DOMAIN

TO_ADDR = "admin@techbaseltd.com"
FROM_ADDR = f"feedback@{EMAIL_DOMAIN}"

# Brand tokens (kept local + minimal — this is an internal notification).
INK = "#0F0B1F"
INK_SOFT = "#565273"
INDIGO = "#5524F5"
LIME = "#D7FF81"
PANEL = "#FBF9F4"
CANVAS = "#ECE9E0"
MUTED = "#6A6580"
SANS = "'Plus Jakarta Sans', Arial, Helvetica, sans-serif"

_CATEGORY_LABELS = {
    "idea": "Idea",
    "problem": "Problem",
    "praise": "Praise",
    "other": "Other",
}


def _row(label: str, value_html: str) -> str:
    return f"""
    <tr>
      <td style="padding:10px 0;border-bottom:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};width:120px;vertical-align:top;">{label}</td>
      <td style="padding:10px 0 10px 16px;border-bottom:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:15px;line-height:1.5;color:{INK};">{value_html}</td>
    </tr>"""


def feedback_html(
    category: str,
    message: str,
    email: Optional[str],
    path: Optional[str],
    user_agent: Optional[str],
) -> str:
    cat_label = _CATEGORY_LABELS.get(category, html.escape(category))
    safe_message = html.escape(message).replace("\n", "<br/>")
    safe_email = html.escape(email) if email else "Not provided"
    email_cell = (
        f'<a href="mailto:{safe_email}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{safe_email}</a>'
        if email
        else f'<span style="color:{MUTED};">Not provided</span>'
    )
    safe_path = html.escape(path) if path else "Unknown"
    safe_ua = html.escape(user_agent) if user_agent else "Unknown"
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="light"/>
<title>Ahavah feedback</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CANVAS};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#ffffff;border-radius:14px;overflow:hidden;">
      <tr><td style="padding:28px 32px 8px;font-family:{SANS};">
        <span style="display:inline-block;padding:7px 13px;border-radius:999px;background:#EDE8FE;color:{INDIGO};font-size:12px;font-weight:800;letter-spacing:0.16em;text-transform:uppercase;">&#9679;&nbsp; New feedback</span>
        <h1 style="margin:16px 0 4px;font-family:{SANS};font-size:24px;font-weight:800;letter-spacing:-0.01em;color:{INK};">{cat_label}</h1>
        <p style="margin:0;font-family:{SANS};font-size:13px;color:{MUTED};">Received {when}</p>
      </td></tr>
      <tr><td style="padding:16px 32px 8px;">
        <div style="background:{PANEL};border-left:4px solid {LIME};border-radius:0 10px 10px 0;padding:16px 18px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK};">
          {safe_message}
        </div>
      </td></tr>
      <tr><td style="padding:12px 32px 28px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          {_row("Reply to", email_cell)}
          {_row("Page", f'<span style="color:{INK_SOFT};">{safe_path}</span>')}
          {_row("Device", f'<span style="color:{INK_SOFT};font-size:12px;">{safe_ua}</span>')}
        </table>
      </td></tr>
    </table>
    <p style="margin:16px 0 0;font-family:{SANS};font-size:12px;color:{MUTED};">Sent by ahavah.app &middot; reply directly to reach the user if they left an email.</p>
  </td></tr>
</table>
</body>
</html>"""


def send_feedback(
    category: str,
    message: str,
    email: Optional[str] = None,
    path: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Synchronous send to the admin inbox. Best-effort (aws_smtp retries then
    gives up without raising). When the user left an email, Reply-To routes
    the admin's reply directly to them instead of bouncing off the noreply
    feedback@ alias (audit Email #7)."""
    from smtp import aws_smtp

    cat_label = _CATEGORY_LABELS.get(category, category)
    aws_smtp.send(
        subject=f"Ahavah feedback: {cat_label}",
        body=feedback_html(category, message, email, path, user_agent),
        to_addr=TO_ADDR,
        from_addr=FROM_ADDR,
        reply_to=email if email else None,
    )


def send_feedback_async(
    category: str,
    message: str,
    email: Optional[str] = None,
    path: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Fire-and-forget so the request returns immediately. Failures are
    swallowed (logged) — feedback is non-critical and we never break the POST."""

    def _go() -> None:
        try:
            send_feedback(category, message, email, path, user_agent)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
