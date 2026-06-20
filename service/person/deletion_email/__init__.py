"""
"Sorry to see you go" email — the soft-delete grace-window notification.

Built on the canonical brand shell (emails.base.render), exactly like every
other lifecycle email (see emails/reengagement.py): real Ahavah logo header,
indigo chip, lime CTA, lime callout, dark-mode + web fonts, bulletproof inline
styling for Gmail. Sent on DELETE /account: warm goodbye + the cutoff date +
the Cancel-deletion recovery + a feedback ask (replies route to a human inbox
via the Reply-To the caller sets).
"""
from service.config import WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    callout,
    INK,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
)

SITE = "https://ahavah.app"


def deletion_pending_template(name: str, purge_iso: str) -> str:
    """Build the HTML body. `purge_iso` is the user-readable cutoff date
    string (e.g. "Fri, June 26, 2026") — the caller formats it."""
    safe_name = (name or "there").strip()

    body_html = f"""
{chip("Account update")}

<h1 class="e-title" style="margin:14px 0 18px;font-family:{SANS};font-size:30px;font-weight:800;letter-spacing:-0.01em;line-height:1.12;color:{INK};">Sorry to see you go</h1>

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Hi {safe_name},
</p>
<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  You asked to delete your Ahavah account, so your profile is now hidden from
  everyone. It will be permanently removed on
  <strong class="e-strong" style="color:{INK};">{purge_iso}</strong>.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Changed your mind? You have until then to keep your profile, photos, matches,
  and chats.
</p>

{button("Cancel deletion", f"{WEB_BASE_URL}/profile", variant="lime", full=True)}

<div style="line-height:30px;height:30px;font-size:0;">&nbsp;</div>

{callout(
    "<strong>Before you go: what could we have done better?</strong><br/>"
    "Whether it was the matches, a missing feature, or just not the right time, "
    "we would genuinely love to know. Just reply to this email and tell us. It "
    "comes straight to our team."
)}

<p class="e-text" style="margin:0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};">
  If you didn't request this, sign in immediately and cancel, as someone may
  have access to your session.
</p>
"""

    footer_html = (
        "Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>"
        "You are receiving this because you asked to delete your account at "
        f'<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.'
    )

    return render(
        title="Sorry to see you go",
        preheader="Your account is scheduled for deletion. Changed your mind? You can still cancel.",
        body_html=body_html,
        footer_html=footer_html,
    )
