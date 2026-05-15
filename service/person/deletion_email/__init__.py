"""
Email template for the soft-delete grace-window notification.

Sent when a user POSTs DELETE /account. Tells them the cutoff date and
how to undo (sign in to the app and tap "Cancel deletion" on /profile).
The cancel-deletion banner that shipped 2026-05-15 only displays when
the user reopens the app — without this email, a user who closes the
app immediately after deletion has no recovery path beyond their
locally-stored session token.

Mirrors the visual style of service/cron/autodeactivate2/template — same
header bar + indigo CTA bar — so the inbox brand cue is consistent
across lifecycle emails.
"""

from service.config import EMAIL_ASSETS_BASE_URL, PRODUCT_NAME, WEB_BASE_URL


def deletion_pending_template(name: str, purge_iso: str) -> str:
    """Build the HTML body. `purge_iso` is the user-readable cutoff
    date string (e.g. "Tue, May 22, 2026") — caller formats it."""
    safe_name = (name or "there").strip()
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Your {PRODUCT_NAME} account is scheduled for deletion</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;">
        <table width="100%" cellspacing="0" cellpadding="0" border="0" align="center">
            <tr>
                <td align="center">
                    <table width="600" cellspacing="0" cellpadding="0" border="0" align="center">
                        <tr>
                            <td bgcolor="#70f" align="center">
                                <img src="{EMAIL_ASSETS_BASE_URL}/header-logo.png" alt="{PRODUCT_NAME} Logo" width="108" height="50" />
                            </td>
                        </tr>
                        <tr>
                            <td bgcolor="#f1e5ff" height="20">&nbsp;</td>
                        </tr>
                        <tr>
                            <td bgcolor="#f1e5ff" align="left" style="color: #70f; padding-left: 30px; padding-right: 30px; padding-bottom: 10px;">
                                <p style="color: #70f; font-size: 22px; font-weight: 900; margin: 0 0 16px 0;">Your account is scheduled for deletion</p>
                                <p style="color: #333; font-size: 16px; line-height: 1.5; margin: 0 0 12px 0;">
                                    Hi {safe_name},
                                </p>
                                <p style="color: #333; font-size: 16px; line-height: 1.5; margin: 0 0 12px 0;">
                                    You requested to delete your {PRODUCT_NAME} account. Your profile is now hidden from everyone, and the account will be permanently removed on <strong>{purge_iso}</strong>.
                                </p>
                                <p style="color: #333; font-size: 16px; line-height: 1.5; margin: 0 0 12px 0;">
                                    Changed your mind? You have until then to keep your profile, photos, matches, and chats. Open the app and tap <em>Cancel deletion</em> on your profile.
                                </p>
                            </td>
                        </tr>
                        <tr>
                          <td bgcolor="#f1e5ff" align="center" style="padding-top: 12px; padding-bottom: 25px;">
                            <table border="0" cellspacing="0" cellpadding="0">
                              <tbody><tr>
                                <td style="border-radius: 50px; border: 3px solid #70f; font-size: 18px; line-height: 26px; color: #70f; text-align: center; min-width: auto !important;">
                                  <a href="{WEB_BASE_URL}/profile" style="display: block; padding: 11px 36px; text-decoration: none; color: #70f;" target="_blank">
                                    <span style="text-decoration: none; color: #70f;">
                                      <strong>Cancel deletion</strong>
                                    </span>
                                  </a>
                                </td>
                              </tr>
                            </tbody></table>
                          </td>
                        </tr>
                        <tr>
                            <td bgcolor="#f1e5ff" align="left" style="color: #666; padding-left: 30px; padding-right: 30px; padding-bottom: 20px;">
                                <p style="color: #666; font-size: 13px; line-height: 1.5; margin: 0;">
                                    If you didn't request this, sign in immediately and cancel — someone may have access to your session.
                                </p>
                            </td>
                        </tr>
                        <tr>
                            <td bgcolor="#70f" height="50">&nbsp;</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
