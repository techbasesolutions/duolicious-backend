"""Brand email base — a faithful port of the canonical Ahavah email design
(emails/canonical/Ahavah-Email-Templates.html, the `.e*` / `.e1*` system).

Bulletproof approach: every element is inline-styled (so Gmail, which strips
<style>, still matches the canonical layout + colours) AND a <style> block
carries the canonical classes, web fonts, and a prefers-color-scheme dark mode
(including a light/dark logo swap) for clients that keep <head> styles
(Apple Mail, iOS Mail). Tokens in emails/README.md.
"""
from __future__ import annotations

import os

EMAIL_ASSET_ORIGIN: str = os.environ.get(
    "AHAVAH_EMAIL_ASSET_ORIGIN", "https://ahavah.app"
)
LOGO_URL: str = f"{EMAIL_ASSET_ORIGIN}/email/logo-horizontal.png"          # dark ink (on light)
LOGO_WHITE_URL: str = f"{EMAIL_ASSET_ORIGIN}/email/logo-horizontal-wht.png"  # white (on dark)

# Tokens (canonical)
INK = "#0F0B1F"
INK_SOFT = "#565273"     # ≈ canonical oklch(0.40 0.05 280) lede
INDIGO = "#5524F5"
LIME = "#D7FF81"
LAVENDER = "#BC96FF"
CANVAS = "#ECE9E0"
PANEL = "#FBF9F4"
MUTED = "#6A6580"        # ≈ canonical oklch(0.45 0.05 280)
SERIF = "'Ultra', Georgia, 'Times New Roman', serif"        # display
SANS = "'Plus Jakarta Sans', Arial, Helvetica, sans-serif"  # body

# Canonical .e__btn: lime pill, ink text, 18px/800, padding 18px 32px, radius 14px.
def button(label: str, href: str, *, variant: str = "lime", full: bool = True) -> str:
    bg = INK if variant == "dark" else LIME
    color = "#ffffff" if variant == "dark" else INK
    width = "width:100%;" if full else ""
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="{width}margin:0;">
      <tr><td align="center" bgcolor="{bg}" style="border-radius:14px;">
        <a href="{href}" target="_blank" style="display:block;padding:18px 32px;font-family:{SANS};font-size:18px;font-weight:800;letter-spacing:0.01em;line-height:1;color:{color};text-decoration:none;border-radius:14px;">{label}</a>
      </td></tr>
    </table>"""


# Outlined display title. Ultra (the canonical title face) does not load in most
# email clients, so they fall back to a generic serif and lose the brand look.
# Instead we reference a pre-rendered PNG of the title in the real Ultra face
# (the glyphs are baked into the image, i.e. "outlined"). `width` is the CSS
# width in px; the PNG itself is exported at 2x for retina. `alt` carries the
# title text so screen readers and image-off clients still get the words.
def title_image(file_name: str, file_name_dark: str, alt: str, width: int) -> str:
    base_style = (
        f"width:{width}px;max-width:100%;height:auto;"
        "margin:18px 0 16px;border:0;outline:none;text-decoration:none;"
    )
    light = f"{EMAIL_ASSET_ORIGIN}/email/{file_name}"       # dark ink, for light bg
    dark = f"{EMAIL_ASSET_ORIGIN}/email/{file_name_dark}"   # white ink, for dark bg
    # Swap mirrors the logo light/dark swap (see _STYLE). Clients that ignore
    # prefers-color-scheme just show the light (dark-ink) image on the light card.
    return (
        f'<img class="e-title-light" src="{light}" alt="{alt}" width="{width}" style="display:block;{base_style}"/>'
        f'<img class="e-title-dark" src="{dark}" alt="{alt}" width="{width}" style="display:none;{base_style}"/>'
    )


# Canonical .e__chip: indigo uppercase pill, 12px/800, 0.16em, leading dot.
def chip(label: str) -> str:
    return (
        f'<span class="e-chip" style="display:inline-block;padding:7px 13px;border-radius:999px;'
        f'background:#EDE8FE;color:{INDIGO};font-family:{SANS};font-size:12px;font-weight:800;'
        f'letter-spacing:0.16em;text-transform:uppercase;">&#9679;&nbsp; {label}</span>'
    )


# Canonical .e1__valid: lime left-border callout (rgba(215,255,129,0.20) bg).
def callout(text: str) -> str:
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 28px;">
      <tr>
        <td width="4" bgcolor="{LIME}" style="background:{LIME};border-radius:4px 0 0 4px;">&nbsp;</td>
        <td class="e-callout" bgcolor="#F4FBE3" style="padding:14px 18px;background:#F4FBE3;border-radius:0 10px 10px 0;font-family:{SANS};font-size:14px;line-height:1.5;color:{INK};font-weight:600;">
          {text}
        </td>
      </tr>
    </table>"""


_STYLE = f"""
  @media (prefers-color-scheme: dark) {{
    .e-card  {{ background:#15121F !important; }}
    .e-head  {{ background:#1A1340 !important; border-color:rgba(255,255,255,0.08) !important; }}
    .e-body  {{ background:#15121F !important; }}
    .e-foot  {{ background:#1A1340 !important; border-color:rgba(255,255,255,0.08) !important; color:#A8A2C8 !important; }}
    .e-title, .e-h2 {{ color:#ffffff !important; }}
    .e-text  {{ color:#C9C4E0 !important; }}
    .e-strong {{ color:#ffffff !important; }}
    .e-callout {{ background:#262017 !important; color:#F4FBE3 !important; }}
    .e-logo-light {{ display:none !important; }}
    .e-logo-dark  {{ display:block !important; }}
    .e-title-light {{ display:none !important; }}
    .e-title-dark  {{ display:block !important; }}
  }}
  .e-logo-dark {{ display:none; }}
  .e-title-dark {{ display:none; }}
"""


def render(
    *,
    title: str,
    preheader: str,
    body_html: str,
    footer_html: str,
    hero_html: str | None = None,
) -> str:
    """Canonical 600px brand shell (white .e card: header + body + footer)."""
    if hero_html is None:
        header_row = f"""<tr><td class="e-head" style="padding:30px 36px 24px;border-bottom:1px solid rgba(15,11,31,0.06);">
        <img class="e-logo-light" src="{LOGO_URL}" alt="Ahavah" height="34" style="height:34px;width:auto;display:block;border:0;outline:none;text-decoration:none;"/>
        <img class="e-logo-dark" src="{LOGO_WHITE_URL}" alt="Ahavah" height="34" style="height:34px;width:auto;border:0;outline:none;text-decoration:none;"/>
      </td></tr>"""
    else:
        header_row = f'<tr><td style="padding:0;">{hero_html}</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="x-apple-disable-message-reformatting"/>
<meta name="color-scheme" content="light dark"/>
<meta name="supported-color-schemes" content="light dark"/>
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Ultra&display=swap" rel="stylesheet"/>
<style>{_STYLE}</style>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CANVAS};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">
      <tr><td class="e-card" style="background:#ffffff;border-radius:14px;overflow:hidden;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family:{SANS};color:{INK};line-height:1.5;">
          {header_row}
          <tr><td class="e-body" style="padding:36px;">
            {body_html}
          </td></tr>
          <tr><td class="e-foot" style="padding:28px 36px;background:{PANEL};border-top:1px solid rgba(15,11,31,0.06);font-family:{SANS};font-size:13px;line-height:1.55;color:{MUTED};text-align:center;">
            {footer_html}
          </td></tr>
        </table>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""
