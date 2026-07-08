"""Marriage checklist results email.

Sent to the respondent AND their spouse when the public checklist activity
is completed. Built on the canonical brand shell (emails.base.render), like
every other lifecycle email. The answers are composed into HTML in-request
and then discarded; nothing is persisted.

Copy rule: NO em dashes anywhere in this template (customer-facing).
"""
from __future__ import annotations

from emails.base import (
    render,
    button,
    chip,
    callout,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SITE = "https://ahavah.app"
SUBJECT = "Your marriage checklist results"

_SECTION_LABELS = {
    "biblical": "Biblical obligations",
    "nice-to-have": "Nice-to-haves",
    "challenge": "Challenges and obstacles",
}
_STANCE_LABELS = {"agree": "Agree", "disagree": "Disagree", "other": "Other"}


def _hearts(importance: int) -> str:
    """Importance 1 to 5 as filled/empty hearts."""
    filled = '<span style="color:#FF4566;">&#10084;</span>' * importance
    empty = f'<span style="color:rgba(15,11,31,0.18);">&#10084;</span>' * (5 - importance)
    return filled + empty


def _answer_row(a: dict) -> str:
    verse = (
        f'<div style="font-family:{SANS};font-size:12px;font-weight:700;color:{INDIGO};margin-top:2px;">{a["verse"]}</div>'
        if a.get("verse") else ""
    )
    role = (
        f'<span style="font-family:{SANS};font-size:11px;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:{MUTED};">{a["role"]} &middot; </span>'
        if a.get("role") else ""
    )
    comment = (
        f'<div style="font-family:{SANS};font-size:13px;line-height:1.5;color:{INK_SOFT};margin-top:6px;">&ldquo;{a["comment"]}&rdquo;</div>'
        if a.get("comment") else ""
    )
    return f"""
    <tr><td style="padding:12px 0;border-bottom:1px solid rgba(15,11,31,0.06);">
      <div>{role}<span style="font-family:{SANS};font-size:15px;font-weight:700;color:{INK};">{a["title"]}</span></div>
      {verse}
      <div style="margin-top:6px;font-family:{SANS};font-size:13px;">
        {_hearts(int(a["importance"]))}
        &nbsp;&nbsp;<span style="font-weight:800;color:{INK};">{_STANCE_LABELS.get(a["stance"], a["stance"])}</span>
      </div>
      {comment}
    </td></tr>"""


def checklist_results_html(name: str | None, answers: list[dict], *, is_spouse_copy: bool = False) -> str:
    safe_name = (name or "there").strip() or "there"

    if is_spouse_copy:
        intro = (
            "Your spouse just completed the Ahavah marriage checklist and "
            "wanted to share their answers with you. Here is what matters "
            "most to them, in their own words."
        )
    else:
        intro = (
            f"Hi {safe_name}, here are your marriage checklist answers. "
            "A copy also went to your spouse so you can talk through them together."
        )

    # Group by section, keep input order inside each.
    sections_html = ""
    for key, label in _SECTION_LABELS.items():
        rows = [a for a in answers if a.get("section") == key]
        if not rows:
            continue
        sections_html += f"""
        <h2 class="e-h2" style="margin:26px 0 4px;font-family:{SANS};font-size:18px;font-weight:800;color:{INK};">{label}</h2>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          {''.join(_answer_row(a) for a in rows)}
        </table>"""

    body_html = f"""
{chip("Marriage checklist")}

<h1 class="e-title" style="margin:14px 0 14px;font-family:{SANS};font-size:26px;font-weight:800;letter-spacing:-0.01em;line-height:1.15;color:{INK};">What matters most</h1>

<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:16px;line-height:1.55;color:{INK_SOFT};">{intro}</p>

{sections_html}

<div style="line-height:26px;height:26px;font-size:0;">&nbsp;</div>

{callout(
    "<strong>A note on privacy.</strong> We did not store these answers. "
    "This email is the only copy, so keep it if you want to revisit the conversation."
)}

{button("Share Ahavah with someone seeking a spouse", SITE, variant="lime", full=True)}
"""

    footer_html = (
        "Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>"
        "You received this because the marriage checklist at "
        f'<a href="{SITE}/marriage-checklist" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app/marriage-checklist</a> '
        "was completed with this email address."
    )

    return render(
        title=SUBJECT,
        preheader="Your checklist answers, ready to talk through together. We did not store them.",
        body_html=body_html,
        footer_html=footer_html,
    )


def send_checklist_results(
    to_email: str,
    name: str | None,
    answers: list[dict],
    *,
    is_spouse_copy: bool = False,
) -> str | None:
    from smtp import aws_smtp

    return aws_smtp.send(
        subject=SUBJECT,
        body=checklist_results_html(name, answers, is_spouse_copy=is_spouse_copy),
        to_addr=to_email,
        reply_to="admin@ahavah.app",
    )
