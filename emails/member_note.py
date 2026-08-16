"""One-off member service notes, ON the canonical template.

Why this exists: two personal notes (Yazy's real-photo request and
Ruth's deletion reminder, 2026-08-10/12) were hand-assembled with an
inline 28px sans heading instead of the Ultra title image, and shipped
visibly off-template. Every one-off note goes through THIS module now,
so the canonical look is structural, not a per-note discipline.

Usage from a droplet one-off script or the REPL:

    from emails.member_note import send_member_note
    send_member_note(
        to_addr="member@example.com",
        subject="Before we say goodbye",
        preheader="One line shown next to the subject.",
        paragraphs=[
            "First paragraph.",
            "Second paragraph with <strong>markup</strong> allowed.",
        ],
        callout_text="Optional emphasized line.",       # or None
        button_label="Open Ahavah",                     # or None
        button_url="https://ahavah.app/discover",
        footer_note="This is the only reminder we will send.",  # or None
        cc_admin=True,   # send an identical copy to the admin inbox
    )

Copy rules: NO em dashes. Sentence case. Not wordy. The title image is
the generic "A note from Ahavah." pair (title-note*.png); a note that
deserves its own headline deserves its own campaign module instead.
"""
from __future__ import annotations

from typing import Optional, Sequence

from service.config import EMAIL_DOMAIN
from emails.base import (
    render,
    button,
    chip,
    title_image,
    callout,
    is_suppressed_send,
    INK_SOFT,
    MUTED,
    SANS,
)

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
ADMIN_COPY_ADDR = "admin@techbaseltd.com"


def member_note_html(
    preheader: str,
    paragraphs: Sequence[str],
    callout_text: Optional[str] = None,
    button_label: Optional[str] = None,
    button_url: Optional[str] = None,
    footer_note: Optional[str] = None,
    subject_for_title: str = "A note from Ahavah",
) -> str:
    paras = "\n".join(
        f'<p class="e-text" style="margin:0 0 16px;font-family:{SANS};'
        f'font-size:17px;line-height:1.55;color:{INK_SOFT};">{p}</p>'
        for p in paragraphs
    )
    body = f"""
{chip("A personal note")}

{title_image("title-note.png", "title-note-wht.png", "A note from Ahavah.", 460)}

{paras}
"""
    if button_label and button_url:
        body += f"\n{button(button_label, button_url)}\n"
    if callout_text:
        body += f'\n<div style="height:20px;line-height:20px;">&nbsp;</div>\n{callout(callout_text)}\n'

    extra_footer = (
        f"{footer_note}<br/>" if footer_note else ""
    )
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
{extra_footer}Questions? Just reply to this email.
<div style="margin-top:14px;">
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=subject_for_title,
        preheader=preheader,
        body_html=body,
        footer_html=footer,
    )


def send_member_note(
    to_addr: str,
    subject: str,
    preheader: str,
    paragraphs: Sequence[str],
    callout_text: Optional[str] = None,
    button_label: Optional[str] = None,
    button_url: Optional[str] = None,
    footer_note: Optional[str] = None,
    cc_admin: bool = True,
) -> bool:
    """Send one service note. Returns False if the recipient is
    suppressed (the admin copy is then also skipped: a suppressed
    member got no email, so there is nothing to file a copy of)."""
    if is_suppressed_send(to_addr):
        return False

    html = member_note_html(
        preheader=preheader,
        paragraphs=paragraphs,
        callout_text=callout_text,
        button_label=button_label,
        button_url=button_url,
        footer_note=footer_note,
        subject_for_title=subject,
    )

    from smtp import make_aws_smtp
    smtp = make_aws_smtp()
    recipients = [to_addr] + ([ADMIN_COPY_ADDR] if cc_admin else [])
    for addr in recipients:
        smtp.send(
            subject=subject,
            body=html,
            to_addr=addr,
            from_addr=FROM_ADDR,
        )
    return True
