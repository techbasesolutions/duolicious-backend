"""Community digest: new members + community size, sent to everyone.

Stats are computed live by the runner (send_digest.py) so the numbers
can never drift from the database. New members are introduced by first
name only, exactly as the app itself shows them.

Copy rules: NO em dashes. Sentence case. Not wordy.
"""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    title_image,
    callout,
    INK_SOFT,
    MUTED,
    SANS,
)

FROM_ADDR = f"support@{EMAIL_DOMAIN}"


def subject_for(new_count: int) -> str:
    if new_count == 1:
        return "A new member joined Ahavah"
    return f"{new_count} new members joined Ahavah"


def _name_list(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def digest_html(
    new_names: list[str],
    total_members: int,
    total_matches: int,
    unsubscribe_url: str,
) -> str:
    names = _name_list(new_names)
    lede = (
        f"Say hello to {names}, who joined in the last two weeks. "
        f"The community now stands at {total_members} members across "
        f"the diaspora, with {total_matches} matches made so far."
    )
    body = f"""
{chip("Community digest")}

{title_image("title-digest.png", "title-digest-wht.png", "The community is growing.", 528)}

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {lede}
</p>

{button("Meet the new members", f"{WEB_BASE_URL}/map")}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("New members appear on the map and in Discover as soon as they join.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(
        title=subject_for(len(new_names)),
        preheader=f"The community is now {total_members} strong.",
        body_html=body,
        footer_html=footer,
    )
