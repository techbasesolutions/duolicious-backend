"""Marriage checklist announcement email.

One-off campaign to the list (waitlist + members): the Marriage Checklist
is live. Sells it three ways: married couples do it together, courting
members send it to the person they are serious about, singles use it for
self-clarity. Built on the canonical brand shell.

Copy rule: NO em dashes anywhere (customer-facing).
"""
from __future__ import annotations

from emails.base import (
    render,
    button,
    chip,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)
from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from service.unsubscribe import make_url as _unsub_url

SITE = "https://ahavah.app"
SUBJECT = "The Marriage Checklist is here. Send it to someone."
PREHEADER = "Work through Scripture, decide what matters to you, and share it. Answers never stored."
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"


def _who(label: str, text: str) -> str:
    return (
        f'<p class="e-text" style="margin:0 0 14px;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">'
        f'<strong style="color:{INK};font-weight:700;">{label}</strong> {text}</p>'
    )


def launch_checklist_html(email: str) -> str:
    body_html = f"""
{chip("New free resource")}

<h1 class="e-title" style="margin:16px 0 12px;font-family:{SANS};font-size:30px;font-weight:800;letter-spacing:-0.02em;line-height:1.1;color:{INK};">The Marriage Checklist<span style="color:{INDIGO};">.</span></h1>

<p class="e-text" style="margin:0 0 14px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK_SOFT};">Hi there,</p>

<p class="e-text" style="margin:0 0 14px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK_SOFT};">We just released something we think you will love. The Marriage Checklist is a free, guided activity: read the passages Scripture sets for a husband and a wife, decide in your own words what each one means to you, rate what matters most, and add your own nice-to-haves and challenges.</p>

<p class="e-text" style="margin:0 0 22px;font-family:{SANS};font-size:16px;line-height:1.6;color:{INK_SOFT};">At the end, your personal summary is emailed to you and whoever you choose. We never store your answers.</p>

<h2 class="e-h2" style="margin:0 0 12px;font-family:{SANS};font-size:18px;font-weight:800;color:{INK};">Who is it for?</h2>

{_who("Married?", "Work through it together and compare summaries over dinner.")}
{_who("Courting, or talking to someone you are serious about?", "Send them your summary, or better, send them the checklist. There are few clearer ways to say you are intentional than asking someone where they stand on Scripture and marriage.")}
{_who("Single and clarifying what you want?", "Complete it for yourself. You will walk away knowing your own non-negotiables.")}

<div style="line-height:10px;height:10px;font-size:0;">&nbsp;</div>

{button("Take the checklist", f"{SITE}/marriage-checklist", variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:13px;color:{MUTED};text-align:center;">Know someone seeking a Torah-observant spouse? Pass this along.</p>
"""

    unsub = _unsub_url("waitlist", email, WEB_BASE_URL)
    link_style = f"color:{MUTED};font-weight:600;text-decoration:underline;"
    footer_html = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You are receiving this because you signed up at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{SITE}/faq" style="{link_style}">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{SITE}/privacy" style="{link_style}">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="{unsub}" style="{link_style}">Unsubscribe</a>
</div>
"""

    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=body_html,
        footer_html=footer_html,
    )
