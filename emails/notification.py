"""Transactional notification emails on the brand shell (emails/base.py).

Each builder is the EMAIL FALLBACK for a notification event (sent only when
push can't reach the user). All share the same brand shell + a single
"Turn off notification emails" unsubscribe link."""
from __future__ import annotations

from emails.base import render, chip, title_image, button, INK_SOFT, MUTED
from service.config import WEB_BASE_URL


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


def _event_email(*, chip_label, title_light, title_dark, title_alt, lede,
                 cta_label, cta_url, unsubscribe_url, subject, preheader) -> str:
    body = f"""
      {chip(chip_label)}
      {title_image(title_light, title_dark, title_alt, 460)}
      <p class="e-text" style="margin:0 0 26px;font-size:16px;line-height:1.55;color:{INK_SOFT};">
        {lede}
      </p>
      {button(cta_label, cta_url, variant="lime", full=True)}
    """
    footer = f"""
      You're getting this because of your Ahavah notification settings.
      <br/>
      <a href="{unsubscribe_url}" style="color:{MUTED};text-decoration:underline;">Turn off notification emails</a>
    """
    return render(title=subject, preheader=preheader,
                  body_html=body, footer_html=footer)


def new_match_email(unsubscribe_url: str) -> str:
    return _event_email(
        chip_label="It's a match",
        title_light="title-match.png", title_dark="title-match-wht.png",
        title_alt="It's a match.",
        lede="You and someone you liked are now connected. Open Ahavah to say hello.",
        cta_label="See your match", cta_url=f"{WEB_BASE_URL}/matches",
        unsubscribe_url=unsubscribe_url,
        subject="You have a new match on Ahavah",
        preheader="Someone you liked liked you back.",
    )


def new_verification_email(tier: str, unsubscribe_url: str) -> str:
    return _event_email(
        chip_label="Verification",
        title_light="title-verified.png", title_dark="title-verified-wht.png",
        title_alt="You're verified.",
        lede=f"Your {tier} verification was approved. Your profile now carries the {tier} trust badge.",
        cta_label="View your profile", cta_url=f"{WEB_BASE_URL}/verify",
        unsubscribe_url=unsubscribe_url,
        subject="You're verified on Ahavah",
        preheader="Your verification was approved.",
    )


def new_like_email(unsubscribe_url: str) -> str:
    return _event_email(
        chip_label="New like",
        title_light="title-like.png", title_dark="title-like-wht.png",
        title_alt="Someone likes you.",
        lede="Someone liked your profile on Ahavah. Open the app to see your likes.",
        cta_label="See your likes", cta_url=f"{WEB_BASE_URL}/matches",
        unsubscribe_url=unsubscribe_url,
        subject="Someone likes you on Ahavah",
        preheader="You have a new like.",
    )
