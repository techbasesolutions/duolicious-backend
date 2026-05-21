"""Brand email base — table-based, fully inline-styled HTML.

Email clients (Gmail, Outlook, Apple Mail) strip <style> blocks, ignore
flexbox/grid, and won't load web fonts, so the canonical design
(emails/canonical/Ahavah-Email-Templates.html) is re-implemented here with
tables + inline styles + web-safe fonts. Tokens documented in emails/README.md.
"""
from __future__ import annotations

import os

# Absolute origin that serves the hosted email logo. Email clients can't load
# relative/local paths, so the logo must live at a public URL on the web origin
# (ahavah-web public/email/logo-horizontal.png). Override via env if the web
# domain moves.
EMAIL_ASSET_ORIGIN: str = os.environ.get(
    "AHAVAH_EMAIL_ASSET_ORIGIN", "https://ahavah.app"
)
LOGO_URL: str = f"{EMAIL_ASSET_ORIGIN}/email/logo-horizontal.png"

# Tokens (see emails/README.md)
INK = "#0F0B1F"
INK_SOFT = "#3A3650"
INDIGO = "#5524F5"
LIME = "#D7FF81"
LAVENDER = "#BC96FF"
CANVAS = "#ECE9E0"
PANEL = "#FBF9F4"
MUTED = "#6A6580"
SERIF = "Georgia, 'Times New Roman', serif"   # display fallback for 'Ultra'
SANS = "Arial, Helvetica, sans-serif"          # body fallback for 'Plus Jakarta Sans'


def button(label: str, href: str, *, variant: str = "lime") -> str:
    """A bulletproof-ish CTA: inline-styled anchor inside a colored table cell."""
    bg = INK if variant == "dark" else LIME
    color = "#ffffff" if variant == "dark" else INK
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
      <tr><td align="center" bgcolor="{bg}" style="border-radius:14px;">
        <a href="{href}" target="_blank" style="display:inline-block;padding:16px 32px;font-family:{SANS};font-size:17px;font-weight:bold;line-height:1;color:{color};text-decoration:none;border-radius:14px;">{label}</a>
      </td></tr>
    </table>"""


def chip(label: str) -> str:
    """Small indigo uppercase pill with a leading dot (the .e__chip component)."""
    return (
        f'<span style="display:inline-block;padding:6px 12px;border-radius:999px;'
        f'background:rgba(85,36,245,0.08);color:{INDIGO};font-family:{SANS};'
        f'font-size:12px;font-weight:bold;letter-spacing:0.14em;text-transform:uppercase;">'
        f"&#9679;&nbsp; {label}</span>"
    )


def render(
    *,
    title: str,
    preheader: str,
    body_html: str,
    footer_html: str,
    hero_html: str | None = None,
) -> str:
    """Wrap body content in the canonical 600px brand shell.

    `hero_html` (optional): a full-bleed top band (e.g. a dark brand hero). When
    omitted, a plain white header with the dark logo is used (transactional
    default). When provided, the hero replaces the header and should include its
    own logo (white logo on a dark hero).
    """
    if hero_html is None:
        header_row = (
            '<tr><td style="padding:28px 36px 22px;border-bottom:1px solid rgba(15,11,31,0.06);">'
            f'<img src="{LOGO_URL}" alt="Ahavah" height="32" style="height:32px;width:auto;display:block;border:0;outline:none;text-decoration:none;"/>'
            "</td></tr>"
        )
    else:
        header_row = f'<tr><td style="padding:0;">{hero_html}</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="x-apple-disable-message-reformatting"/>
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CANVAS};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;background:#ffffff;border-radius:14px;overflow:hidden;">
      {header_row}
      <tr><td style="padding:36px;font-family:{SANS};color:{INK};">
        {body_html}
      </td></tr>
      <tr><td style="padding:26px 36px;background:{PANEL};border-top:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:13px;line-height:1.55;color:{MUTED};text-align:center;">
        {footer_html}
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# White logo lockup for dark heroes (host alongside the dark logo).
LOGO_WHITE_URL: str = f"{EMAIL_ASSET_ORIGIN}/email/logo-horizontal-wht.png"
