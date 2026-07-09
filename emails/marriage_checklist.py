"""Marriage checklist results email.

The SAME email goes to the respondent and their spouse (the design frames
it as one shared summary: "We sent the same note to you and your spouse").
Built on the canonical brand shell (emails.base.render). Composed from the
posted answers in-request and then discarded; nothing is persisted.

Faithful to the Claude Design export ("Ahavah Marriage Checklist Email"):
chip, display title, lede, ranked rows (reference, section + frequency,
"What it means to me." / "How I would carry it out." / "Note.", importance
dots, stance chip), lime Share CTA, privacy footer.

Copy rule: NO em dashes anywhere (customer-facing).
"""
from __future__ import annotations

import html as _html

from emails.base import (
    render,
    button,
    chip,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SITE = "https://ahavah.app"
SUBJECT = "Your marriage checklist summary"

_SECTION_LABELS = {
    "biblical": "Biblical Obligations",
    "nice-to-have": "Nice to Haves",
    "challenge": "Challenges and Obstacles",
}
_STANCE = {
    "agree": ("Agree", "#D3F8DF", "#06310f"),
    "disagree": ("Disagree", "#FF4566", "#ffffff"),
    "other": ("Other", "#BC96FF", "#1a0c3d"),
}
_FREQ = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}


def _dots(importance: int) -> str:
    cells = "".join(
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
        f'background:{"#5524F5" if n <= importance else "rgba(15,11,31,0.12)"};'
        f'margin-right:3px;"></span>'
        for n in (1, 2, 3, 4, 5)
    )
    return f'<div style="margin-top:8px;line-height:1;">{cells}</div>'


def _note(label: str, text: str) -> str:
    """`text` is attacker-controlled free text: ALWAYS escaped here."""
    return (
        f'<div style="font-family:{SANS};font-size:13px;line-height:1.5;color:{INK_SOFT};margin-top:6px;">'
        f'<strong style="color:{INK};font-weight:700;">{label} </strong>{_html.escape(text)}</div>'
    )


def _row(n: int, a: dict) -> str:
    # Every user-supplied field is HTML-escaped before interpolation. This
    # email is composed from free text a user typed and can be sent to an
    # arbitrary spouse address, so unescaped HTML here would be a phishing
    # vector from our verified domain (review finding, 2026-07-08).
    heading = _html.escape(a.get("ref") or a.get("title") or "")
    meta = _SECTION_LABELS.get(a.get("section", ""), "")
    if a.get("frequency"):
        meta += f' &middot; {_FREQ.get(a["frequency"], a["frequency"])}'
    stance = a.get("stance")
    stance_chip = ""
    if stance in _STANCE:
        stance_label, stance_bg, stance_fg = _STANCE[stance]
        stance_chip = (
            f'<span style="display:inline-block;font-family:{SANS};font-size:10px;font-weight:800;'
            f'letter-spacing:0.04em;text-transform:uppercase;padding:4px 8px;border-radius:7px;'
            f'background:{stance_bg};color:{stance_fg};">{stance_label}</span>'
        )

    notes = ""
    if a.get("comment"):
        notes += _note("What it means to me.", a["comment"])
    examples = [e.strip() for e in (a.get("examples") or []) if e and e.strip()]
    if examples:
        notes += _note("How I would carry it out.", "; ".join(examples))
    if a.get("stance") == "other" and a.get("other_note"):
        notes += _note("Note.", a["other_note"])

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:{'none' if n == 1 else '1px solid rgba(15,11,31,0.06)'};">
      <tr>
        <td width="40" valign="top" style="padding:16px 0;">
          <div style="width:28px;height:28px;border-radius:8px;background:#0F0B1F;color:#EFFFC8;font-family:{SANS};font-weight:800;font-size:13px;text-align:center;line-height:28px;">{n}</div>
        </td>
        <td valign="top" style="padding:16px 0;">
          <div style="font-family:{SANS};font-size:15px;font-weight:700;color:{INK};">{heading}</div>
          <div style="font-family:{SANS};font-size:12px;color:{MUTED};margin-top:3px;">{meta}</div>
          {notes}
          {_dots(int(a.get("importance", 0)))}
        </td>
        <td width="70" valign="top" align="right" style="padding:16px 0;">
          {stance_chip}
        </td>
      </tr>
    </table>"""


def checklist_results_html(role: str | None, answers: list[dict]) -> str:
    # Highest-rated first, mirroring the activity's summary.
    ordered = sorted(answers, key=lambda a: -int(a.get("importance", 0)))
    role_part = f" as the {role}" if role in ("husband", "wife") else ""

    rows = "".join(_row(i + 1, a) for i, a in enumerate(ordered))

    body_html = f"""
{chip("Marriage Checklist")}

<h1 class="e-title" style="margin:16px 0 12px;font-family:{SANS};font-size:30px;font-weight:800;letter-spacing:-0.02em;line-height:1.1;color:{INK};">What matters most to you<span style="color:{INDIGO};">.</span></h1>

<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">Here is the summary you completed{role_part}. We sent the same note to you and your spouse, so you can read it side by side and talk it through together.</p>

{rows}

<div style="line-height:28px;height:28px;font-size:0;">&nbsp;</div>

{button("Share Ahavah", SITE, variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:13px;color:{MUTED};text-align:center;">Know someone seeking a Torah-observant spouse? Pass this along.</p>
"""

    footer_html = (
        "We never stored your answers. This summary was composed when you sent "
        "it, then your responses were discarded.<br/>"
        "Reply to this email to reach a real person. "
        f'<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">Ahavah</a>, made for the diaspora.'
    )

    return render(
        title=SUBJECT,
        preheader="The summary you completed, sent to you both. We never stored your answers.",
        body_html=body_html,
        footer_html=footer_html,
    )


def send_checklist_results(to_email: str, role: str | None, answers: list[dict]) -> str | None:
    from smtp import aws_smtp

    return aws_smtp.send(
        subject=SUBJECT,
        body=checklist_results_html(role, answers),
        to_addr=to_email,
        reply_to="admin@ahavah.app",
    )
