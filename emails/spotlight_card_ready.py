"""E4 card ready: the member-triggered request to review and approve their
own Spotlight card (spec 3.1, 3.4, 3.5, section 2). Canonical shell.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

import threading
import traceback
from html import escape as html_escape

from database import api_tx
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS
from service.campaigns.runner import run_campaign
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
        preheader="Review your card, choose your photo, and approve or skip.",
        body_html=body,
        footer_html=footer,
    )


def send_card_ready(person_id: int, request_key: str) -> bool:
    """Synchronous send. Reads the member's live email/name and the
    request's kind inside one read-committed transaction, then runs the
    E4 campaign for that single recipient. `exempt=True` because this is
    member-triggered (a new welcome/member-of-week card is a direct
    consequence of the member's own eligibility, not a broadcast), and the
    campaign_id is keyed on request_key so a retried caller cannot double
    send for the same request."""
    with api_tx('read committed') as tx:
        person = tx.execute(_Q_PERSON, dict(id=person_id)).fetchone()
        candidate = tx.execute(_Q_KIND, dict(rk=request_key)).fetchone()
    if not person or not candidate:
        return False
    kind_label = _KIND_LABELS.get(candidate['kind'], candidate['kind'])

    def build(row: dict) -> tuple[str, str]:
        with api_tx() as tx:
            cu = card_url(tx, request_key, row['email'])
        if cu is None:
            # No activated person matches this email any more (the member
            # was deactivated between the lookup above and this build call,
            # or the recipient row is otherwise stale) -- raising here
            # makes run_campaign count this as a failure instead of
            # sending, or dry-running, a card with href="None".
            raise ValueError('no_person')
        unsub = unsub_url(UNSUB_SCOPE, row['email'], WEB_BASE_URL)
        html = card_ready_html(row['first_name'], kind_label, cu,
                               CARD_TOKEN_TTL_SECONDS // 86400, unsub)
        return SUBJECT, html

    recipient = dict(person_id=person_id, email=person['email'], first_name=person['first_name'])
    result = run_campaign(
        api_tx, 'e4', f'e4-{request_key}', [recipient], build, send=True,
        from_addr=FROM_ADDR, unsub_scope=UNSUB_SCOPE,
        list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>",
        exempt=True)
    return result['sent'] == 1


def send_card_ready_async(person_id: int, request_key: str) -> None:
    """Fire-and-forget from the admin/growth spotlight routes; failures are
    swallowed (the candidate row already exists, the email is a nicety)."""
    def _go() -> None:
        try:
            send_card_ready(person_id, request_key)
        except Exception:
            print(traceback.format_exc())

    threading.Thread(target=_go, daemon=True).start()
