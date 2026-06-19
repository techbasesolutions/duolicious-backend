"""
Email template for the soft-delete grace-window notification — the
"Sorry to see you go" email.

Sent when a user POSTs DELETE /account. Warm goodbye + the cutoff date +
how to undo (Cancel deletion) + a feedback ask (replies route to a human
inbox via the Reply-To the caller sets). On-brand: Ahavah cream canvas,
Plus Jakarta Sans, indigo/lavender palette, rounded card — matches the
in-app design system and the branded lifecycle emails (NOT the legacy
generic #70f/Arial style this replaced).
"""

from service.config import PRODUCT_NAME, WEB_BASE_URL

# Brand tokens (light-mode, for inbox rendering) — mirror the app design
# system: --indigo #5524F5, lavender, cream canvas, ink, Plus Jakarta Sans.
_INDIGO = "#5524F5"
_LAVENDER_SOFT = "#EDE8FE"
_INK = "#0F0B1F"
_INK_SOFT = "#565273"
_MUTED = "#6A6580"
_CANVAS = "#ECE9E0"
_PANEL = "#FFFFFF"
_SANS = "'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif"


def deletion_pending_template(name: str, purge_iso: str) -> str:
    """Build the HTML body. `purge_iso` is the user-readable cutoff date
    string (e.g. "Fri, June 26, 2026") — the caller formats it."""
    safe_name = (name or "there").strip()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Sorry to see you go</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{_CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_CANVAS};">
  <tr><td align="center" style="padding:32px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">

      <tr><td align="center" style="padding-bottom:18px;">
        <span style="font-family:{_SANS};font-size:22px;font-weight:800;letter-spacing:-0.01em;color:{_INDIGO};">Ahavah</span>
      </td></tr>

      <tr><td style="background:{_PANEL};border-radius:20px;box-shadow:0 8px 24px rgba(15,11,31,0.08);">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">

          <tr><td style="padding:32px 36px 4px;">
            <span style="display:inline-block;padding:6px 13px;border-radius:999px;background:{_LAVENDER_SOFT};color:{_INDIGO};font-family:{_SANS};font-size:11px;font-weight:800;letter-spacing:0.14em;text-transform:uppercase;">Account update</span>
            <h1 style="margin:16px 0 0;font-family:{_SANS};font-size:28px;font-weight:800;letter-spacing:-0.02em;color:{_INK};">Sorry to see you go</h1>
          </td></tr>

          <tr><td style="padding:14px 36px 0;font-family:{_SANS};font-size:16px;line-height:1.6;color:{_INK_SOFT};">
            <p style="margin:0 0 14px;">Hi {safe_name},</p>
            <p style="margin:0 0 14px;">You asked to delete your {PRODUCT_NAME} account, so we've hidden your profile. It will be permanently removed on <strong style="color:{_INK};">{purge_iso}</strong>.</p>
            <p style="margin:0;">Changed your mind? You have until then to keep your profile, photos, matches, and chats.</p>
          </td></tr>

          <tr><td align="center" style="padding:22px 36px 4px;">
            <a href="{WEB_BASE_URL}/profile" target="_blank" style="display:inline-block;padding:14px 34px;border-radius:999px;background:{_INDIGO};color:#ffffff;font-family:{_SANS};font-size:16px;font-weight:800;text-decoration:none;">Cancel deletion</a>
          </td></tr>

          <tr><td style="padding:24px 36px 8px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_LAVENDER_SOFT};border-radius:16px;">
              <tr><td style="padding:18px 20px;font-family:{_SANS};">
                <p style="margin:0 0 6px;font-size:15px;font-weight:800;color:{_INK};">Before you go: what could we have done better?</p>
                <p style="margin:0;font-size:14px;line-height:1.55;color:{_INK_SOFT};">Whether it was the matches, a missing feature, or just not the right time, we'd genuinely love to know. Just reply to this email and tell us. It comes straight to our team.</p>
              </td></tr>
            </table>
          </td></tr>

          <tr><td style="padding:14px 36px 32px;font-family:{_SANS};font-size:12px;line-height:1.5;color:{_MUTED};">
            If you didn't request this, sign in immediately and cancel, as someone may have access to your session.
          </td></tr>

        </table>
      </td></tr>

      <tr><td align="center" style="padding:18px 0 0;font-family:{_SANS};font-size:12px;color:{_MUTED};">
        Ahavah &#128156;
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""
