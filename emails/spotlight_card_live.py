"""E5 card live: the member's Spotlight card has published (spec 3.1, 3.4,
3.5, section 2). Canonical shell. Copy rules: NO em dashes. Sentence case.

Instagram permalink note: the publishing worker (Task 6) stores whatever id
the platform API returns for `external_post_id`. Facebook's post id doubles
as a stable permalink path, but Instagram's real web permalink needs the
post's shortcode, which the Graph API returns on a separate lookup that
Task 7 (removal/lifecycle) is the first to need and store. Until that lands,
`post_url_for('instagram', ...)` links to the public Ahavah Instagram
profile instead of guessing a shortcode from the media id (which would
produce a broken link)."""
from __future__ import annotations

import threading
import traceback
from html import escape as html_escape
from urllib.parse import quote

from database import api_tx
from emails.base import render, button, chip, title_image, INK_SOFT, MUTED, SANS
from service.campaigns import make_campaign_link
from service.campaigns.runner import run_campaign
from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from service.unsubscribe import make_url as unsub_url

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "Your Spotlight is live"
UNSUB_SCOPE = 'notifications'

# Until Task 7 stores the real Instagram permalink shortcode (see module
# docstring), share/see-the-post links for Instagram posts point here.
AHAVAH_IG_HANDLE = "ahavah.app"

_Q_PERSON = """
    SELECT email, split_part(name, ' ', 1) AS first_name
      FROM person WHERE id = %(id)s
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


def send_card_live(person_id: int, request_key: str, external_post_id: str, platform: str) -> bool:
    """Synchronous send for one platform's publish. `campaign_id` includes
    the platform since facebook and instagram publish (and so E5-fire)
    independently for the same request_key. The share CTA wraps the post
    URL in a /s/<key> campaign link (make_campaign_link) so shares
    attribute clicks back to this post; the plain "see the post" link is
    left unwrapped."""
    with api_tx('read committed') as tx:
        person = tx.execute(_Q_PERSON, dict(id=person_id)).fetchone()
        card = tx.execute(_Q_CARD, dict(rk=request_key, pl=platform)).fetchone()
    if not person or not card or not card['image_url']:
        return False
    post_url = post_url_for(platform, external_post_id)

    def build(row: dict) -> tuple[str, str]:
        with api_tx() as tx:
            wrapped = make_campaign_link(tx, f'post:{request_key}', post_url, person_id)
        html = card_live_html(row['first_name'], card['image_url'], post_url,
                              share_url_for(wrapped),
                              unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL))
        return SUBJECT, html

    recipient = dict(person_id=person_id, email=person['email'], first_name=person['first_name'])
    result = run_campaign(
        api_tx, 'e5', f'e5-{request_key}-{platform}', [recipient], build, send=True,
        from_addr=FROM_ADDR, unsub_scope=UNSUB_SCOPE,
        list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>",
        exempt=True)
    return result['sent'] == 1


def send_card_live_async(person_id: int, request_key: str, external_post_id: str, platform: str) -> None:
    """Fire-and-forget from the admin/growth spotlight routes; failures are
    swallowed (the post is already live, the email is a nicety)."""
    def _go() -> None:
        try:
            send_card_live(person_id, request_key, external_post_id, platform)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
