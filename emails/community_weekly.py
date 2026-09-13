"""E2 weekly community email (spec 3.5). Replaces emails/digest.py.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

from typing import Optional
from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "This week on Ahavah"

def _spotlight_block(sp: dict) -> str:
    return f"""
<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};font-weight:700;">Member of the week</p>
<a href="{sp['post_url']}" style="text-decoration:none;">
  <img src="{sp['image_url']}" alt="{sp['first_name']}, {sp['age']}, {sp['country']}" width="520" style="display:block;width:100%;max-width:520px;border-radius:16px;margin:0 0 10px;" />
</a>
<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{sp['first_name']}, {sp['age']} <span style="color:{MUTED};">in {sp['country']}</span></p>
"""

def _newcomers_block(rows: list[dict]) -> str:
    if not rows:
        return f'<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">No new members this week. Know someone who belongs here? Your invite link is in your profile.</p>'
    items = "".join(f'<li style="margin:0 0 6px;">{r["first_name"]}' + (f' <span style="color:{MUTED};">in {r["country"]}</span>' if r.get('country') else '') + '</li>' for r in rows)
    return f'<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};font-weight:700;">New this week</p><ul style="margin:0 0 20px;padding-left:20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{items}</ul>'

def community_weekly_html(new_members: list[dict], total_members: int, spotlight: Optional[dict], cta_url: str, unsubscribe_url: str) -> str:
    body = f"""
{chip("Community")}

{title_image("title-community.png", "title-community-wht.png", "This week on Ahavah.", 460)}

{_spotlight_block(spotlight) if spotlight else ''}

{_newcomers_block(new_members)}

<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  The community is now <strong>{total_members} members</strong> across borders.
</p>

{button("Open Discover", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Want to be featured? Turn on Spotlight under Settings, Privacy.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving the weekly community email as a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Stop the weekly email</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(title=SUBJECT, preheader="New members, community size, and this week's spotlight.", body_html=body, footer_html=footer)
