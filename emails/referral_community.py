"""Referral-community email — one-shot blast to the beta cohort.

Same family as emails/referral_intro.py, but the angle is COMMUNITY rather
than individual insider status: Ahavah is a niche space that only reaches
critical mass if its own members carry it into their local communities. Asks
the recipient to share their personal link within their circles and to follow
Ahavah on social so the community can gather there before launch.

Headline is a styled serif text heading (not a title_image PNG) so the email
is sendable without a new rendered asset. Inline-styled via emails.base;
dark-mode + suppression + unsubscribe handled by the family conventions. Send
is gated by a CLI (not fired by any route)."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    callout,
    title_image,
    is_suppressed_send,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "Let's build this within our community"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "A niche app like ours grows one community at a time. Here is how to help it take root."
SITE = "https://ahavah.app"

# Social presence. Threads + Instagram share the @ahavah.app handle.
_INSTAGRAM = "https://instagram.com/ahavah.app"
_THREADS = "https://www.threads.net/@ahavah.app"
_FACEBOOK = "https://www.facebook.com/people/Ahavah/61590464442249/"


def _social_pill(label: str, href: str) -> str:
    # Mirrors the brand chip (lavender-tint fill, indigo text, rounded) so the
    # row reads as part of the design system. inline-block wraps cleanly on
    # narrow screens with no flexbox dependency.
    return (
        f'<a href="{href}" target="_blank" '
        f'style="display:inline-block;margin:0 8px 8px 0;padding:10px 18px;'
        f'border-radius:999px;background:#EDE8FE;color:{INDIGO};'
        f'font-family:{SANS};font-size:14px;font-weight:700;'
        f'text-decoration:none;">{label}</a>'
    )


def _body(code: str) -> str:
    share_url = f"{WEB_BASE_URL}/share/{code}"
    plaintext_url = f"{WEB_BASE_URL}/i/{code}"
    return f"""
{chip("Small by design")}

{title_image("title-community.png", "title-community-wht.png", "Built by our own.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Shalom. Ahavah is not for everyone, and that is the point. It is built for
  our community: Torah-observant believers who are serious about marriage,
  family, and a covenant home. A space this specific will never trend its way
  into existence. It grows the way our community always has, one trusted
  introduction at a time.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  That is where you come in. A circle this close-knit reaches the people it is
  meant for only when its own members carry it forward. Share your invite link
  with the believers around you: your congregation, your fellowship, your
  family, the friends you would trust with someone's heart.
</p>

{callout("When someone you bring finishes their profile at launch, we credit your account with 5 tokens. That is one Boost: a 30-minute spotlight on Discover, ready for the moment you want to be seen.")}

{button("Share with your community &rarr;", share_url, variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};text-align:center;">
  Or copy and paste your link:
</p>
<p class="e-text" style="margin:4px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{INDIGO};text-align:center;word-break:break-all;">
  <a href="{plaintext_url}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{plaintext_url}</a>
</p>

<hr style="height:1px;background:rgba(15,11,31,0.08);border:none;margin:30px 0 26px;"/>

<h2 class="e-h2" style="margin:0 0 10px;font-family:{SANS};font-size:20px;line-height:1.2;font-weight:800;letter-spacing:-0.01em;color:{INK};">
  Follow @ahavah.app
</h2>
<p class="e-text" style="margin:0 0 18px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  We are gathering the community on social as we count down to launch. Follow
  along, and tell others where to find us.
</p>
<div style="margin:0 0 4px;">
  {_social_pill("Instagram", _INSTAGRAM)}
  {_social_pill("Threads", _THREADS)}
  {_social_pill("Facebook", _FACEBOOK)}
</div>

<p class="e-text" style="margin:26px 0 0;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  As one of the first to join, you are more than an early user. You are part of
  the foundation. What our community builds now is what everyone who comes after
  will find waiting for them. Thank you for building it with us.
</p>
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you opted into the Ahavah beta at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
</div>
"""


def referral_community_html(email: str, code: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(code),
        footer_html=_footer(email),
    )


def send_referral_community(email: str, code: str) -> None:
    """Synchronous send. Skips suppressed addresses (example.com /
    techbaseltd.com). Best-effort (aws_smtp retries then gives up
    without raising)."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    aws_smtp.send(
        subject=SUBJECT,
        body=referral_community_html(email, code),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=(
            f"<mailto:admin@ahavah.app?subject=Unsubscribe>, <{unsub}>"
        ),
    )
