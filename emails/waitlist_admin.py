"""Admin notification on a new waitlist signup.

Sent to TO_ADDR whenever a brand-new waitlist row is created, so the team is
notified of each new signup (separate from the welcome email the signer-upper
receives). Internal ops notification: a clean, branded-lite card. ALL user-
supplied content (email + answers) is HTML-escaped. Best-effort send (aws_smtp
retries then gives up without raising); the async helper swallows failures.
"""
from __future__ import annotations

import html
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional

from service.config import EMAIL_DOMAIN

TO_ADDR = "admin@techbaseltd.com"
FROM_ADDR = f"waitlist@{EMAIL_DOMAIN}"

INK = "#0F0B1F"
INK_SOFT = "#565273"
INDIGO = "#5524F5"
LIME = "#D7FF81"
PANEL = "#FBF9F4"
CANVAS = "#ECE9E0"
MUTED = "#6A6580"
SANS = "'Plus Jakarta Sans', Arial, Helvetica, sans-serif"


def _row(label: str, value_html: str) -> str:
    return f"""
    <tr>
      <td style="padding:9px 0;border-bottom:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:12px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};width:130px;vertical-align:top;">{label}</td>
      <td style="padding:9px 0 9px 16px;border-bottom:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:15px;line-height:1.5;color:{INK};">{value_html}</td>
    </tr>"""


def _answer_rows(answers: Optional[dict]) -> str:
    out = []
    for k, v in (answers or {}).items():
        if v in (None, "", [], {}):
            continue
        val = ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)
        label = html.escape(str(k).replace("_", " ").title())
        out.append(_row(label, f'<span style="color:{INK_SOFT};">{html.escape(val)}</span>'))
    return "".join(out)


def new_signup_html(
    email: str,
    answers: Optional[dict],
    count: Optional[int],
    *,
    mode: str = "signup",      # "signup" | "completed" | "beta"
    beta: Optional[bool] = None,
) -> str:
    safe_email = html.escape(email)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if mode == "completed":
        chip_label = "Onboarding complete"
        count_line = "Someone completed onboarding."
    elif mode == "beta":
        chip_label = "New beta tester"
        count_line = f"{count} beta testers now." if count else "New beta tester."
    else:
        chip_label = "New signup"
        count_line = f"Waitlist is now at {count}." if count else "New waitlist signup."
    email_cell = f'<a href="mailto:{safe_email}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{safe_email}</a>'
    # Beta-status row (only when known). Green Yes / muted No so it reads at a glance.
    beta_row = ""
    if beta is not None:
        val = ('<span style="color:#3F8F2E;font-weight:700;">Yes</span>'
               if beta else f'<span style="color:{MUTED};font-weight:700;">No</span>')
        beta_row = _row("Beta tester", val)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="light"/>
<title>New waitlist signup</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CANVAS};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#ffffff;border-radius:14px;overflow:hidden;">
      <tr><td style="padding:28px 32px 8px;font-family:{SANS};">
        <span style="display:inline-block;padding:7px 13px;border-radius:999px;background:#EDE8FE;color:{INDIGO};font-size:12px;font-weight:800;letter-spacing:0.16em;text-transform:uppercase;">&#9679;&nbsp; {html.escape(chip_label)}</span>
        <h1 style="margin:16px 0 4px;font-family:{SANS};font-size:24px;font-weight:800;letter-spacing:-0.01em;color:{INK};">{count_line}</h1>
        <p style="margin:0;font-family:{SANS};font-size:13px;color:{MUTED};">{when}</p>
      </td></tr>
      <tr><td style="padding:12px 32px 28px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          {_row("Email", email_cell)}
          {beta_row}
          {_answer_rows(answers)}
        </table>
      </td></tr>
    </table>
    <p style="margin:16px 0 0;font-family:{SANS};font-size:12px;color:{MUTED};">Sent by ahavah.app on each new waitlist signup.</p>
  </td></tr>
</table>
</body>
</html>"""


def send_new_signup_notice(email: str, answers: Optional[dict] = None, count: Optional[int] = None, beta: Optional[bool] = None) -> None:
    """Synchronous send to the admin inbox. Skips sample addresses. Best-effort."""
    if not email or email.endswith("@example.com"):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject="New Ahavah waitlist signup",
        body=new_signup_html(email, answers, count, mode="signup", beta=beta),
        to_addr=TO_ADDR,
        from_addr=FROM_ADDR,
    )


def send_new_signup_notice_async(email: str, answers: Optional[dict] = None, count: Optional[int] = None, beta: Optional[bool] = None) -> None:
    """Fire-and-forget so the signup response isn't blocked; failures swallowed
    (the row is already saved)."""
    if not email or email.endswith("@example.com"):
        return

    def _go() -> None:
        try:
            send_new_signup_notice(email, answers, count, beta)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()


def send_onboarding_complete_notice(email: str, answers: Optional[dict] = None, beta: Optional[bool] = None) -> None:
    """Synchronous admin notice: a signer-upper completed the demographic
    onboarding (waitlist row gained answers). Best-effort."""
    if not email or email.endswith("@example.com"):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject="Ahavah onboarding completed",
        body=new_signup_html(email, answers, None, mode="completed", beta=beta),
        to_addr=TO_ADDR,
        from_addr=FROM_ADDR,
    )


def send_beta_optin_notice(email: str, count: Optional[int] = None) -> None:
    """Synchronous admin notice when someone opts into the beta cohort."""
    if not email or email.endswith("@example.com"):
        return
    from smtp import aws_smtp

    aws_smtp.send(
        subject="New Ahavah beta tester",
        body=new_signup_html(email, None, count, mode="beta", beta=True),
        to_addr=TO_ADDR,
        from_addr=FROM_ADDR,
    )


def send_beta_optin_notice_async(email: str, count: Optional[int] = None) -> None:
    """Fire-and-forget; failures swallowed (the beta_signup row is the truth)."""
    if not email or email.endswith("@example.com"):
        return

    def _go() -> None:
        try:
            send_beta_optin_notice(email, count)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()


def send_onboarding_complete_notice_async(email: str, answers: Optional[dict] = None, beta: Optional[bool] = None) -> None:
    """Fire-and-forget; failures swallowed (the row is already saved)."""
    if not email or email.endswith("@example.com"):
        return

    def _go() -> None:
        try:
            send_onboarding_complete_notice(email, answers, beta)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
