"""E3 re-invite for members quiet for 30 days (spec 3.5). Canonical shell.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

import html as _html

from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "New faces on Ahavah since you were here"

def _esc(value) -> str:
    """Member-supplied text reaches the HTML, so escape it. Same convention as
    emails/feedback.py and emails/marriage_checklist.py."""
    return _html.escape(str(value), quote=True)


def _names_block(newcomers: list[dict]) -> str:
    items = "".join(
        f'<li style="margin:0 0 6px;">{_esc(n["first_name"])}'
        + (f' <span style="color:{MUTED};">in {_esc(n["country"])}</span>' if n.get('country') else '')
        + '</li>'
        for n in newcomers)
    return f'<ul style="margin:0 0 20px;padding-left:20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{items}</ul>'

def reinvite_html(first_name: str, newcomers: list[dict], total_new: int, cta_url: str, unsubscribe_url: str) -> str:
    noun = "new member" if total_new == 1 else "new members"
    greeting_name = _esc(first_name)
    body = f"""
{chip("Since you were away")}

{title_image("title-reinvite.png", "title-reinvite-wht.png", "New faces since you were away.", 486)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {greeting_name}, {total_new} {noun} who match what you are looking for have
  joined since you were last here. A few of them:
</p>

{_names_block(newcomers)}

{button("See who joined", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Your profile, matches and messages are exactly as you left them.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(title=SUBJECT, preheader=f"{total_new} {noun} joined since your last visit.", body_html=body, footer_html=footer)
