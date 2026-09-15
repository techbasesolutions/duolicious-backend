"""E5 card live: the member's Spotlight card has published (spec 3.1, 3.4,
3.5, section 2). Canonical shell. Copy rules: NO em dashes. Sentence case.

Instagram permalink note: the admin worker (Task 6) resolves Instagram's
real web permalink via a separate Graph lookup (the id alone is not enough
to build one) and sends it through the `/complete` receipt's `post_url`
field, alongside Facebook's own id-derived path. `enqueue_card_live` below
uses that `post_url` as-is whenever it is an `https://` string. `post_url_for
('instagram', ...)` is only the FALLBACK for a receipt that arrives with no
usable `post_url` (an older worker, or a failed lookup): it links to the
public Ahavah Instagram profile instead of guessing a shortcode from the
media id, which would produce a broken link."""
from __future__ import annotations

from html import escape as html_escape
from urllib.parse import quote

from emails.base import render, button, chip, title_image, INK_SOFT, MUTED, SANS
from service.campaigns import make_campaign_link, outbox
from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from service.unsubscribe import make_url as unsub_url

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "Your Spotlight is live"
UNSUB_SCOPE = 'notifications'

# Until Task 7 stores the real Instagram permalink shortcode (see module
# docstring), share/see-the-post links for Instagram posts point here.
AHAVAH_IG_HANDLE = "ahavah.app"

# `activated AND spotlight_opt_in` is part of the recipient query itself: by
# the time a receipt lands the member may have withdrawn or been deactivated,
# and "your card is live" is the one email that must never reach someone who
# has left Community Spotlight. No row means no send (see `send_card_live`).
_Q_PERSON = """
    SELECT email, split_part(name, ' ', 1) AS first_name
      FROM person WHERE id = %(id)s AND activated AND spotlight_opt_in
"""

_Q_CARD = """
    SELECT image_url FROM publishing_queue
     WHERE request_key = %(rk)s AND platform = %(pl)s
"""


def post_url_for(platform: str, external_post_id: str) -> str:
    if platform == 'facebook':
        return f"https://www.facebook.com/{external_post_id}"
    if platform == 'instagram':
        return f"https://www.instagram.com/{AHAVAH_IG_HANDLE}/"
    raise ValueError('bad_platform')


def share_url_for(post_url: str) -> str:
    return f"https://www.facebook.com/sharer/sharer.php?u={quote(post_url, safe='')}"


def card_live_html(first_name: str, image_url: str, post_url: str, share_url: str, unsubscribe_url: str) -> str:
    if not str(image_url).startswith('https://'):
        raise ValueError('image_url must be https')
    if not str(post_url).startswith('https://'):
        raise ValueError('post_url must be https')
    name = html_escape(first_name, quote=True)
    img = html_escape(image_url, quote=True)
    post = html_escape(post_url, quote=True)
    body = f"""
{chip("Spotlight")}

{title_image("title-spotlight.png", "title-spotlight-wht.png", "Meet Spotlight.", 430)}

<img src="{img}" width="520" alt="Your Spotlight card" style="display:block;width:100%;max-width:520px;height:auto;margin:0 0 20px;border:0;outline:none;border-radius:12px;"/>

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {name}, your card is up on the Ahavah page. Share it with the people who
  should see it.
</p>

{button("Share your card", share_url)}

<div style="height:16px;line-height:16px;">&nbsp;</div>

<p class="e-text" style="margin:0;font-family:{SANS};font-size:15px;line-height:1.5;color:{MUTED};">
  <a href="{post}" style="color:{MUTED};font-weight:600;text-decoration:underline;">See the post</a>
</p>
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you opted in to Community Spotlight.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(
        title=SUBJECT,
        preheader="Your Spotlight card is live. Share it with the people who should see it.",
        body_html=body,
        footer_html=footer,
    )


def enqueue_card_live(tx, person_id: int, request_key: str, external_post_id: str, platform: str,
                      post_url: str | None = None) -> int | None:
    """Queue E5 inside the CALLER'S transaction (F07). Returns the outbox row
    id, or None when there is nothing to send.

    This used to be a synchronous send fired from a daemon thread after the
    receipt had already committed, so a restart between the two lost the
    email outright. Enqueuing on the receipt's own transaction means the
    publish and the "your card is live" message commit together, and the
    cron drains it afterwards. The /s/ campaign link is minted on the same
    `tx` for the same reason: no link row for a message that rolled back.

    `campaign_id` includes the platform since facebook and instagram publish
    (and so E5-fire) independently for the same request_key.

    `post_url`: the worker's own receipt (Task 6) may carry the real post
    URL it got back from the platform (Instagram's permalink lookup, in
    particular) -- when it is an `https://` string it is used as-is;
    otherwise this falls back to `post_url_for`, whose Instagram branch
    still only links to the public profile (module docstring).

    The "Share your card" button's own click is what gets attributed: its
    href is a /s/<key> campaign link (make_campaign_link) whose target is
    the Facebook sharer dialog for this post (share_url_for(post_url), a
    www.facebook.com URL even when the post itself is on Instagram, since
    Facebook's sharer can open for any link), so clicking it in the email
    is counted against 'post:<request_key>' the same way a click on the
    plain post link is. The plain "see the post" link stays the unwrapped,
    plain post URL, so a reader can always reach the post directly even if
    campaign-link redirects are ever unavailable."""
    person = tx.execute(_Q_PERSON, dict(id=person_id)).fetchone()
    card = tx.execute(_Q_CARD, dict(rk=request_key, pl=platform)).fetchone()
    if not person or not card or not card['image_url']:
        return None
    if isinstance(post_url, str) and post_url.startswith('https://'):
        resolved_post_url = post_url
    else:
        resolved_post_url = post_url_for(platform, external_post_id)
    # external_ok=True: the sharer dialog lives on www.facebook.com, not our
    # own web app, which make_campaign_link otherwise refuses (spec 3.4/3.5
    # CTA attribution for E5).
    wrapped = make_campaign_link(tx, f'post:{request_key}', share_url_for(resolved_post_url),
                                 person_id, external_ok=True)
    unsub = unsub_url(UNSUB_SCOPE, person['email'], WEB_BASE_URL)
    html = card_live_html(person['first_name'], card['image_url'], resolved_post_url, wrapped, unsub)
    return outbox.enqueue(
        tx, campaign='e5', campaign_id=f'e5-{request_key}-{platform}', person_id=person_id,
        email=person['email'], subject=SUBJECT, html=html, from_addr=FROM_ADDR,
        unsub_scope=UNSUB_SCOPE,
        list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        exempt=True)
