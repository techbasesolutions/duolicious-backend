"""Transactional purchase-receipt emails on the brand shell (emails/base.py).

Sent unconditionally after a successful Stripe Checkout completes. Unlike
emails/notification.py these are RECEIPTS, not notifications: they are never
gated by notification_preference and carry no unsubscribe link (transactional
1:1 mail, exempt from the bulk-unsubscribe requirement). They share the same
brand shell + a single pre-rendered title image pair (title-receipt*.png)."""
from __future__ import annotations

from emails.base import render, chip, title_image, button, callout, INK_SOFT
from service.config import WEB_BASE_URL


def _receipt(*, lede, callout_text, cta_label, cta_url, subject, preheader) -> str:
    body = f"""
      {chip("Receipt")}
      {title_image("title-receipt.png", "title-receipt-wht.png", "Payment received.", 460)}
      <p class="e-text" style="margin:0 0 22px;font-size:16px;line-height:1.55;color:{INK_SOFT};">
        {lede}
      </p>
      {callout(callout_text)}
      {button(cta_label, cta_url, variant="lime", full=True)}
    """
    footer = "You're getting this because you made a purchase on Ahavah."
    return render(title=subject, preheader=preheader,
                  body_html=body, footer_html=footer)


def tokens_purchased_email(*, count: int, amount_label: str | None = None) -> str:
    word = "token" if count == 1 else "tokens"
    have = "has" if count == 1 else "have"
    charged = f" We charged {amount_label}." if amount_label else ""
    return _receipt(
        lede=(f"Your purchase is confirmed.{charged} {count} {word} {have} been added "
              f"to your balance, ready to spend on Boosts, Super Likes, and Rewinds."),
        callout_text=f"+{count} {word} added to your balance",
        cta_label="Open your tokens →", cta_url=f"{WEB_BASE_URL}/profile/tokens",
        subject="Your Ahavah purchase is confirmed",
        preheader=f"{count} {word} added to your balance.",
    )


# Subscription tier_key -> human cadence word for the receipt copy.
_PLAN_LABEL = {"month": "monthly", "quart": "quarterly", "year": "yearly"}


def premium_started_email(*, tier_key: str, stipend: int,
                          amount_label: str | None = None) -> str:
    plan = _PLAN_LABEL.get(tier_key, "")
    plan_phrase = f"{plan} " if plan else ""
    charged = f" We charged {amount_label}." if amount_label else ""
    stipend_line = (f" {stipend} tokens have been added to your balance for this cycle."
                    if stipend else "")
    return _receipt(
        lede=(f"Welcome to Ahavah Premium.{charged} Your {plan_phrase}subscription is now "
              f"active.{stipend_line}"),
        callout_text=(f"Premium active · +{stipend} tokens this cycle" if stipend
                      else "Premium active"),
        cta_label="Manage your subscription →", cta_url=f"{WEB_BASE_URL}/profile",
        subject="Welcome to Ahavah Premium",
        preheader="Your Premium subscription is active.",
    )
