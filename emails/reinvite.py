"""E3, who has joined since you were last online (spec 3.5, reshaped
2026-09-19). Canonical shell.

One campaign, two states of reader:

  `quiet`   an activated member who has not liked, passed or messaged for a
            while. Nothing is wrong with their account.
  `paused`  a member the dormancy cron deactivated after 30 days offline
            (service/cron/autodeactivate2). Their profile is hidden until
            they sign in, which reactivates it (service/person/sql: signing
            in sets activated = TRUE).

Owner decisions 2026-09-19:
  * counts, never names, and never the word "faces" while no face is shown.
    Members consented to a Spotlight card, not to being named in a campaign
    email. Names and cards return here when enough members have opted in.
  * the count runs from when the reader was last online, not from their last
    like or message.

Copy rules: NO em dashes. Sentence case. Torah-observant believers, never
Jewish framing.
"""
from __future__ import annotations

import html as _html

from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, callout, INK, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"

# The campaign's own name, used by the admin index and the send log. Each
# reader gets the subject for their state, from subject_for().
SUBJECT = "Who has joined since you were last online"

_SUBJECTS = {
    'quiet': "Who has joined since you were last online",
    'paused': "Your Ahavah profile is paused, and the community has grown",
}


def subject_for(state: str) -> str:
    return _SUBJECTS.get(state, SUBJECT)


def _esc(value) -> str:
    """Member-supplied text reaches the HTML, so escape it. Same convention as
    emails/feedback.py and emails/marriage_checklist.py."""
    return _html.escape(str(value), quote=True)


def _singularise(plural: str) -> str:
    """women -> woman, men -> man, new members -> new member."""
    return {'women': 'woman', 'men': 'man'}.get(plural, plural[:-1] if plural.endswith('s') else plural)


def _headline(text: str) -> str:
    """A text headline where the other campaigns use an Ultra title image.
    The existing title-reinvite.png reads "New faces since you were away",
    which would promise faces this email does not show. A replacement image
    is on the design brief; until it lands, the headline is live text."""
    return (f'<h1 style="margin:18px 0 16px;font-family:{SANS};font-size:34px;line-height:1.15;'
            f'font-weight:800;letter-spacing:-0.01em;color:{INK};">{text}</h1>')


def reinvite_html(first_name: str, total_new: int, cta_url: str, unsubscribe_url: str,
                  gender_label: str = "new members", state: str = "quiet") -> str:
    who = gender_label or "new members"
    verb = "has" if total_new == 1 else "have"
    subject_phrase = _singularise(who) if total_new == 1 else who
    name = _esc(first_name)
    count_line = f"{total_new} {subject_phrase} {verb} joined Ahavah since you were last online"

    if state == 'paused':
        body = f"""
{chip("Your profile is paused")}

{_headline("Your place is held.")}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {name}, your profile is paused because you have been away, so nobody can see
  you at the moment. Nothing is lost. Your photos, answers, matches and
  messages are exactly where you left them.
</p>

<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {count_line}, and they match what you are looking for.
</p>

{button("Bring my profile back", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Signing in is all it takes. Your profile goes live again the moment you do.")}
"""
        preheader = f"One sign-in restores everything. {count_line}."
    else:
        body = f"""
{chip("Since you were last online")}

{_headline("The community has grown.")}

<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {name}, {count_line}, and they match what you are looking for.
</p>

{button("See who joined", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Your profile, matches and messages are exactly as you left them.")}
"""
        preheader = f"{count_line}."

    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(title=subject_for(state), preheader=preheader, body_html=body, footer_html=footer)
