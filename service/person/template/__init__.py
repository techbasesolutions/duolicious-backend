"""Sign-in OTP email.

Brand-consistent port of the canonical E1 "verify" template
(emails/canonical/Ahavah-Email-Templates.html): the shared .e shell (logo
header, dark-mode aware), indigo chip, outlined Ultra title image, six code
boxes, a lime "valid for N minutes" callout, and the canonical footer. Built
on emails.base so it matches the waitlist / beta emails exactly, replacing the
off-brand base Duolicious template.
"""
import html

from service.config import PRODUCT_NAME
from emails.base import (
    render,
    chip,
    callout,
    title_image,
    INK,
    INK_SOFT,
    MUTED,
    PANEL,
    SANS,
)

# Mirrors the sign-in OTP expiry in service/person/sql (INTERVAL '10 minutes').
OTP_TTL_MINUTES = 10


def _code_boxes(otp: str) -> str:
    box = (
        "border:1.5px solid rgba(15,11,31,0.12);border-radius:12px;"
        f"background:{PANEL};font-family:{SANS};font-size:30px;font-weight:800;"
        f"color:{INK};letter-spacing:0.02em;"
    )
    cells = (
        f'<td align="center" valign="middle" height="60" style="height:60px;{box}">{html.escape(d)}</td>'
        for d in otp
    )
    inner = '<td width="8" style="width:8px;">&nbsp;</td>'.join(cells)
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="width:100%;margin:0 0 24px;table-layout:fixed;">
      <tr>{inner}</tr>
    </table>"""


def otp_template(otp: str) -> str:
    body = f"""
{chip("Sign in")}

{title_image("title-otp.png", "title-otp-wht.png", "Your sign-in code.", 528)}

<p class="e-text" style="margin:0 0 24px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Enter this code in the Ahavah app to finish signing in.
</p>

{_code_boxes(otp)}

{callout(f"Valid for {OTP_TTL_MINUTES} minutes.")}

<p class="e-text" style="margin:24px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};">
  If you didn't request this, you can ignore this email. No one can sign in
  without the code.
</p>
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because someone requested a sign-in code for this email.
<div style="margin-top:14px;">
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/legal/terms" style="color:{MUTED};font-weight:600;text-decoration:underline;">Terms</a>
</div>
"""
    return render(
        title=f"Sign in to {PRODUCT_NAME}",
        preheader=f"Your Ahavah sign-in code. Valid for {OTP_TTL_MINUTES} minutes.",
        body_html=body,
        footer_html=footer,
    )
