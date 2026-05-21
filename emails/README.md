# Ahavah Email Templates — canonical source of truth

`canonical/Ahavah-Email-Templates.html` is the **visual** source of truth for all
Ahavah transactional/lifecycle emails, exported from Claude Design
(claude.ai/design). When building a new email, match this design exactly.

It contains 9 reference templates inside desktop-client previews (600px body):

| Ref | Template | From address |
| --- | -------- | ------------ |
| E1 | Welcome / verify email (6-digit code) | hello@ahavah.app |
| E2 | New match | matches@ahavah.app |
| E3 | New message | messages@ahavah.app |
| E4 | Weekly digest / re-engagement | digest@ahavah.app |
| E5 | Bronze verified | trust@ahavah.app |
| E6 | Premium upgrade | hello@ahavah.app |
| E7 | Someone liked you (premium tease) | — |
| E8 | Security alert / new device | — |
| E9 | Beta launch invite (ticket) | — |

## CRITICAL: the canonical HTML is NOT directly sendable

The canonical file uses modern CSS — class selectors, flexbox/grid, `oklch()`,
external Google Fonts. Real email clients (Gmail, Outlook, Apple Mail) strip
`<style>` blocks, ignore flexbox, and won't load web fonts reliably. **Every
sent email must be re-implemented as table-based, fully inline-styled HTML**
(see `service/person/template/__init__.py` `otp_template` for the existing
pattern). Treat the canonical file as the pixel/visual spec, not as code to ship.

## Design tokens (extracted from the canonical file)

**Fonts** — `'Plus Jakarta Sans'` (body), `'Ultra'` (display headings). In sent
email, fall back to a web-safe stack: headings `Georgia, 'Times New Roman', serif`;
body `Arial, Helvetica, sans-serif` (web fonts won't load in most clients).

**Colors**
- Ink / text: `#0F0B1F`
- Indigo (primary accent): `#5524F5`
- Lime (primary CTA / brand pop): `#D7FF81`
- Lavender (secondary accent): `#BC96FF`
- Canvas (email backdrop): `#ECE9E0`
- Panel (cards / footer): `#FBF9F4`
- Surface: `#FFFFFF`
- Muted text: `#6A6580` (approx of the canonical `oklch(0.45 0.05 280)`)
- Tier: bronze `#CD7F32`, silver `#C0C0C0`, gold `#FFD700`
- Alert: `#FF4566`

**Layout** — 600px body width, centered on the canvas color, white inner card
(radius 12–18px in design; keep simple in email).

**Shared components** (`.e*` classes in the canonical file)
- `.e__head` — white header band with the horizontal logo (36px tall)
- `.e__body` — 36px padding content area
- `.e__footer` — centered, `#FBF9F4` background, muted text + Help/Privacy/Terms links
- `.e__btn` — lime pill button, ink text, 800 weight, radius 14px (`--outline`, `--dark`, `--full` variants)
- `.e__chip` — small indigo uppercase pill with a leading dot
- Headings: `Ultra` display, with one `<em>` word in indigo (or lime on dark heroes)

## Brand assets

`canonical/assets/brand/` holds the logo lockups. For **sent** email the logo
must be referenced by an absolute public URL (clients can't load relative/local
paths). Host the needed logo on the web origin (e.g. `public/email/...` served
at the production domain) and reference that URL.

## Building a new template

1. Open the matching reference in `canonical/Ahavah-Email-Templates.html`.
2. Re-implement it as table-based, inline-styled HTML in a Python generator
   under `emails/` (or `service/<area>/template/`), using the tokens above.
3. Send via `smtp.aws_smtp.send(subject=, body=<html>, to_addr=, from_addr=)`.
4. Test-render against Gmail + Apple Mail before any bulk send.
