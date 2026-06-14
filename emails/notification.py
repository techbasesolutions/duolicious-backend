"""Transactional notification emails on the brand shell (emails/base.py).

Phase 1 covers the new-message fallback only. Other events (match,
verification) will add sibling builders here in later phases."""
from __future__ import annotations

from emails.base import render, chip, title_image, button, INK_SOFT, MUTED


def new_message_email(*, headline: str, open_url: str, unsubscribe_url: str) -> str:
    body = f"""
      {chip("New message")}
      {title_image("title-message.png", "title-message-wht.png", headline, 460)}
      <p class="e-text" style="margin:0 0 26px;font-size:16px;line-height:1.55;color:{INK_SOFT};">
        {headline} Open Ahavah to read and reply.
      </p>
      {button("Open Ahavah →", open_url, variant="lime", full=True)}
    """
    footer = f"""
      You're getting this because you have unread messages on Ahavah.
      <br/>
      <a href="{unsubscribe_url}" style="color:{MUTED};text-decoration:underline;">Turn off message emails</a>
    """
    return render(
        title="You have a new message on Ahavah",
        preheader="Someone is waiting to hear back from you.",
        body_html=body,
        footer_html=footer,
    )
