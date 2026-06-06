"""POST /referral-click — fire-and-forget click logger.

Hit by ahavah-web's /i/<code> Route Handler in parallel with the redirect
setup. Records every hit on the referral landing so we can see whether
links are being clicked even when the user never signs up. Public,
unauthenticated. Best-effort: if the insert fails, we still return 200
so the FE's fire-and-forget call never throws."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import post, validate
from database import api_tx


def _classify_ua(ua: str) -> str:
    """Cheap coarse classification — good enough for analytics."""
    if not ua:
        return "unknown"
    lower = ua.lower()
    # Common email/social-media preview crawlers
    if any(s in lower for s in (
        "bot", "spider", "crawler", "preview", "facebookexternalhit",
        "twitterbot", "linkedinbot", "slackbot", "discordbot",
        "telegrambot", "whatsapp", "applebot", "googleimageproxy",
    )):
        return "bot"
    if "mobile" in lower or "android" in lower or "iphone" in lower:
        return "mobile"
    return "desktop"


_Q_LOOKUP_INVITER = """
    SELECT email FROM beta_signup WHERE referral_code = %(code)s
"""

_Q_INSERT_CLICK = """
    INSERT INTO referral_link_click
        (code, inviter_email, well_formed, user_agent_class, user_agent)
    VALUES
        (%(code)s, %(inviter_email)s, %(well_formed)s, %(uac)s, %(ua)s)
"""


@post('/referral-click')
@validate(t.PostReferralClick)
def post_referral_click(req: t.PostReferralClick):
    code = (req.code or "").upper()[:64]
    ua = (req.user_agent or "")[:512]
    uac = _classify_ua(ua)
    try:
        with api_tx() as tx:
            inviter = None
            if req.well_formed:
                row = tx.execute(_Q_LOOKUP_INVITER, dict(code=code)).fetchone()
                if row:
                    inviter = row["email"]
            tx.execute(
                _Q_INSERT_CLICK,
                dict(
                    code=code,
                    inviter_email=inviter,
                    well_formed=bool(req.well_formed),
                    uac=uac,
                    ua=ua,
                ),
            )
    except Exception:
        # Best-effort. Never let click logging break the FE.
        pass
    return {"ok": True}
