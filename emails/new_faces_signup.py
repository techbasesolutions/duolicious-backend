"""E6 new faces, for people who created an account but never finished
onboarding. Canonical shell.

Distinct from onboarding_nudge (a one-off "finish your profile" reminder with
no community news) and from E3 reinvite (activated members who went quiet).
This one gives the unfinished signup a reason to come back: who has joined
since they started, counted, never named.

Owner decision 2026-09-19: counts only. These readers are not members, so
naming members to them would share more than anyone agreed to. Names and
faces belong to Spotlight, where the member consents to each card.

Copy rules: NO em dashes. Sentence case. Torah-observant believers, never
Jewish framing.
"""
from __future__ import annotations

import html as _html

from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "Your profile is still waiting, and the community has grown"


def _esc(value) -> str:
    return _html.escape(str(value), quote=True)


def _singularise(plural: str) -> str:
    return {'women': 'woman', 'men': 'man'}.get(plural, plural[:-1] if plural.endswith('s') else plural)


def new_faces_signup_html(first_name: str | None, joined_count: int, gender_label: str,
                          country_count: int, cta_url: str, unsubscribe_url: str) -> str:
    """`joined_count` of `gender_label` have joined since this person signed
    up. `country_count` is how many countries the whole community now spans,
    which is true of the community and names nobody."""
    who = gender_label or 'new members'
    subject_phrase = _singularise(who) if joined_count == 1 else who
    verb = 'has' if joined_count == 1 else 'have'
    greeting = f"{_esc(first_name)}, you" if first_name else "You"

    if joined_count:
        news = f"""
<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Since then, {joined_count} {subject_phrase} {verb} joined Ahavah. The
  community now spans {country_count} countries, and none of them can see you
  yet.
</p>
"""
    else:
        news = f"""
<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  The community now spans {country_count} countries, and none of them can see
  you yet.
</p>
"""

    body = f"""
{chip("Still waiting")}

{title_image("title-finish-profile.png", "title-finish-profile-wht.png", "Finish your profile.", 447)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {greeting} started an Ahavah profile and stopped partway. It is still there,
  exactly as you left it.
</p>

{news}

{button("Finish my profile", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("It takes about two minutes. A photo and a few answers, and the community can see you.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you started creating an Ahavah profile.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    preheader = (f"{joined_count} {subject_phrase} joined since you signed up."
                 if joined_count else "Your profile is two minutes from done.")
    return render(title=SUBJECT, preheader=preheader, body_html=body, footer_html=footer)
