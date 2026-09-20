"""E4 card ready: the member-triggered request to review and approve their
own Spotlight card (spec 3.1, 3.4, 3.5, section 2). Canonical shell.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

from html import escape as html_escape

from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS
from service.campaigns import outbox
from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from service.spotlight.approval import CARD_TOKEN_TTL_SECONDS, card_url
from service.unsubscribe import make_url as unsub_url

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "Your Spotlight card is ready"
UNSUB_SCOPE = 'notifications'

_KIND_LABELS = {
    'welcome': 'a new member of the community',
    'member_of_week': 'member of the week',
    'highlight': 'a member highlight',
}

_Q_PERSON = """
    SELECT email, split_part(name, ' ', 1) AS first_name
      FROM person WHERE id = %(id)s AND activated
"""

_Q_KIND = """
    SELECT kind FROM publishing_queue WHERE request_key = %(rk)s LIMIT 1
"""


def card_ready_html(first_name: str, kind_label: str, card_url: str, expires_days: int, unsubscribe_url: str) -> str:
    name = html_escape(first_name, quote=True)
    label = html_escape(kind_label, quote=True)
    body = f"""
{chip("Spotlight")}

{title_image("title-card-ready.png", "title-card-ready-wht.png", "Your Spotlight card is ready.", 486)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {name}, we would like to feature you as {label}. Nothing is posted until
  you say so. Once your card preview is ready, you can review it, choose
  the photo you prefer, and approve or skip.
</p>

{button("Review my card", card_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout(f"This link works for {expires_days} days. If it expires, nothing is posted.")}
"""
    footer = f"""
Ahavah &middot; Matchmaking for Torah-observant believers.<br/>
You're receiving this because you opted in to Community Spotlight.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(
        title=SUBJECT,
        preheader="Review your card, choose your photo, and approve or skip.",
        body_html=body,
        footer_html=footer,
    )


def enqueue_card_ready(tx, person_id: int, request_key: str) -> int | None:
    """Queue E4 inside the CALLER'S transaction (F07). Returns the outbox row
    id, or None when there is nothing to send.

    This used to be a synchronous send fired from a daemon thread, which
    meant the invite could be lost three separate ways: the thread died with
    the process, an SMTP hiccup dropped it, and a retried request could send
    it twice. Enqueuing here instead ties the invite to the very transaction
    that created (or re-confirmed) the candidate: either both land or
    neither does, and the cron drains it afterwards.

    The card nonce is minted on this same `tx` for the same reason -- a token
    must never exist for a message that was rolled back.

    `exempt=True` because this is member-triggered (a new welcome or
    member-of-week card is a direct consequence of the member's own
    eligibility, not a broadcast), and the campaign_id is keyed on
    request_key so a retried caller cannot double send for the same request.

    None is returned rather than raised for the three ordinary "nothing to
    send" cases: no activated person, no candidate, or no card token. The
    caller is mid-transaction, and aborting a candidate creation because an
    email could not be addressed would be the wrong trade."""
    person = tx.execute(_Q_PERSON, dict(id=person_id)).fetchone()
    candidate = tx.execute(_Q_KIND, dict(rk=request_key)).fetchone()
    if not person or not candidate:
        return None
    cu = card_url(tx, request_key, person['email'])
    if cu is None:
        # No activated person matches this email any more, so there is no
        # nonce to mint against. Queuing anyway would send a card with
        # href="None".
        return None
    kind_label = _KIND_LABELS.get(candidate['kind'], candidate['kind'])
    unsub = unsub_url(UNSUB_SCOPE, person['email'], WEB_BASE_URL)
    html = card_ready_html(person['first_name'], kind_label, cu,
                           CARD_TOKEN_TTL_SECONDS // 86400, unsub)
    return outbox.enqueue(
        tx, campaign='e4', campaign_id=f'e4-{request_key}', person_id=person_id,
        email=person['email'], subject=SUBJECT, html=html, from_addr=FROM_ADDR,
        unsub_scope=UNSUB_SCOPE,
        list_unsubscribe=f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub}>",
        exempt=True, requires_spotlight_opt_in=True)
