# Referral-intro email — design spec

**Date:** 2026-06-05
**Status:** Approved, awaiting implementation plan
**Parent spec:** [`2026-06-05-beta-referrals-design.md`](./2026-06-05-beta-referrals-design.md) (section "Email + CLI")
**Implementer:** TBD via `writing-plans` skill

## Purpose

A one-shot email blast to the current beta cohort (currently 15 rows in `beta_signup`) introducing each recipient's personal referral link and explaining the 5-token reward. The first user-visible touchpoint of the referrals feature; all subsequent referral conversations (in-app, future emails) will lean on the framing this email establishes.

## Aesthetic direction

*Hand-written invitation note.* Where the other family members sound like brand → recipient (`welcome`, `beta_welcome`, `beta_launch`), this one is founders → trusted insider. The shift is entirely carried by copy + one new chip token (`You + 1`); visual primitives stay identical so the family still reads as one system.

## Hard constraints (re-asserted from parent spec)

- Reuse `emails/base.py` shell — `render()`, `chip()`, `button()`, `callout()`, `title_image()`, `is_suppressed_send()`, all existing tokens.
- One new title PNG pair (`title-referral.png` + `title-referral-wht.png`), 528px × 2x raster, transparent, Ultra typeface, INK-on-light + white+LAVENDER-on-dark variants. Trailing period in LAVENDER (matches existing family).
- Footer unsubscribe via `service.unsubscribe.make_url('beta', email, WEB_BASE_URL)`.
- Pythonic `.py` template emitting HTML via `emails.base.render()`. No React.
- Personal link appears twice: once as the lime CTA button, once below as a copyable plaintext URL with `word-break: break-all`.
- Body covers (a) what the link is, (b) what it does for the recipient, (c) what it does for the friend, (d) the reward math in concrete in-economy terms.
- Tone: warm, recipient-as-trusted-insider. Not "share to win" promo-bro.

## Final copy choices

| Slot | Final |
|---|---|
| Subject | **Your link to bring someone in** |
| Preheader | **You earn a Boost for each friend who joins through your link.** |
| Chip | **You + 1** |
| Title image (Ultra, dark + white variants) | **Bring someone with you.** — trailing period in LAVENDER |
| CTA button label | **Share your link →** |

## Body copy (ready to interpolate)

After the chip + title image, the body renders these blocks in order. Tokens reference `emails.base.SANS / INK_SOFT / MUTED` etc.

### Block 1 — opening (acknowledge trust)

```
Shalom. You were one of the first people to opt into the Ahavah beta.
We are not running this loudly — we are building it one trusted person
at a time. That is why we are writing to you specifically.
```

Style: `font-family: {SANS}; font-size: 17px; line-height: 1.55; color: {INK_SOFT}; margin: 0 0 16px;`

### Block 2 — the ask + framing

```
Here is your personal invite link. Send it to one Torah-observant
friend you would want to see meet someone good. When they sign up
through it, your name is on the door for them.
```

Same style as Block 1.

### Block 3 — reward (in a callout)

```
{callout("When that friend finishes their profile at launch, we credit
your account with 5 tokens. That is one Boost — a 30-minute spotlight
on Discover. Hold it for the right moment.")}
```

The `callout()` helper already styles this as a lime-panel emphasis block.

### Block 4 — CTA button

```
{button("Share your link &rarr;", share_url, variant="lime", full=True)}
```

Where `share_url = f"{WEB_BASE_URL}/share/{code}"`.

### Block 5 — plaintext URL backup

Below the button, in a small caption block:

```html
<p class="e-text" style="margin: 14px 0 0; font-family: {SANS};
   font-size: 14px; line-height: 1.6; color: {MUTED}; text-align: center;">
  Or copy and paste this link:
</p>
<p class="e-text" style="margin: 4px 0 0; font-family: {SANS};
   font-size: 14px; line-height: 1.6; color: {INDIGO};
   text-align: center; word-break: break-all;">
  <a href="{plaintext_url}" style="color: {INDIGO};
     font-weight: 600; text-decoration: none;">{plaintext_url}</a>
</p>
```

Where `plaintext_url = f"{WEB_BASE_URL}/i/{code}"`.

**Note: two different URLs are intentional.**
- `share_url` (`/share/<code>`) is the inviter's action surface — opens Web Share API
- `plaintext_url` (`/i/<code>`) is what the **invitee** lands on — what the inviter pastes

### Block 6 — soft close (no chase)

```
No deadline. No chase. Pick someone you would actually want at a
Shabbat table. That is the same instinct we are asking you to follow.
```

Style: `font-family: {SANS}; font-size: 15px; line-height: 1.6; color: {INK_SOFT}; margin: 24px 0 0;`

### Block 7 — footer (unsubscribe-equipped)

Standard family footer, via:

```python
unsub = make_url("beta", email, WEB_BASE_URL)
```

Footer HTML mirrors `beta_welcome.py`'s `_footer()`:

```
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you opted into the Ahavah beta at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;
  text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;
    text-decoration:underline;">Unsubscribe</a>
</div>
```

## Template module skeleton

```python
# emails/referral_intro.py
from __future__ import annotations
from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render, button, chip, callout, title_image,
    is_suppressed_send,
    INK_SOFT, INDIGO, MUTED, SANS,
)
from service.unsubscribe import make_url as _unsub_url

SUBJECT = "Your link to bring someone in"
FROM_ADDR = f"hello@{EMAIL_DOMAIN}"
PREHEADER = "You earn a Boost for each friend who joins through your link."
SITE = "https://ahavah.app"


def _body(code: str) -> str:
    share_url = f"{WEB_BASE_URL}/share/{code}"
    plaintext_url = f"{WEB_BASE_URL}/i/{code}"
    return f"""
{chip("You + 1")}

{title_image("title-referral.png", "title-referral-wht.png", "Bring someone with you.", 528)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Shalom. You were one of the first people to opt into the Ahavah beta.
  We are not running this loudly &mdash; we are building it one trusted
  person at a time. That is why we are writing to you specifically.
</p>
<p class="e-text" style="margin:0 0 28px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Here is your personal invite link. Send it to one Torah-observant
  friend you would want to see meet someone good. When they sign up
  through it, your name is on the door for them.
</p>

{callout("When that friend finishes their profile at launch, we credit your account with 5 tokens. That is one Boost &mdash; a 30-minute spotlight on Discover. Hold it for the right moment.")}

{button("Share your link &rarr;", share_url, variant="lime", full=True)}

<p class="e-text" style="margin:14px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED};text-align:center;">
  Or copy and paste this link:
</p>
<p class="e-text" style="margin:4px 0 0;font-family:{SANS};font-size:14px;line-height:1.6;color:{INDIGO};text-align:center;word-break:break-all;">
  <a href="{plaintext_url}" style="color:{INDIGO};font-weight:600;text-decoration:none;">{plaintext_url}</a>
</p>

<p class="e-text" style="margin:24px 0 0;font-family:{SANS};font-size:15px;line-height:1.6;color:{INK_SOFT};">
  No deadline. No chase. Pick someone you would actually want at a
  Shabbat table. That is the same instinct we are asking you to follow.
</p>
"""


def _footer(email: str) -> str:
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    return f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You&apos;re receiving this because you opted into the Ahavah beta at
<a href="{SITE}" style="color:{INDIGO};font-weight:600;text-decoration:none;">ahavah.app</a>.
<div style="margin-top:14px;">
  <a href="{unsub}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
</div>
"""


def referral_intro_html(email: str, code: str) -> str:
    return render(
        title=SUBJECT,
        preheader=PREHEADER,
        body_html=_body(code),
        footer_html=_footer(email),
    )


def send_referral_intro(email: str, code: str) -> None:
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    aws_smtp.send(
        subject=SUBJECT,
        body=referral_intro_html(email, code),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=f"<mailto:admin@ahavah.app?subject=Unsubscribe>, <{unsub}>",
    )
```

## Title image render

Two PNGs generated by the existing CDP-driven render script (the one previously used for `title-welcome`, `title-beta-welcome`, etc.). Source HTML for each:

**Light variant** (`title-referral.png`):

```html
<div style="font-family: 'Ultra'; font-size: 92px; color: #0F0B1F;
            background: transparent; padding: 24px;">
  Bring someone with you<span style="color: #BC96FF;">.</span>
</div>
```

**Dark variant** (`title-referral-wht.png`):

```html
<div style="font-family: 'Ultra'; font-size: 92px; color: #FFFFFF;
            background: transparent; padding: 24px;">
  Bring someone with you<span style="color: #BC96FF;">.</span>
</div>
```

Both rendered at `1056 × N` (2× the email's 528px target) with `Emulation.setDefaultBackgroundColorOverride({r:0,g:0,b:0,a:0})` for transparency, saved to `ahavah-web/public/email/title-referral.png` + `title-referral-wht.png`.

## What `writing-plans` needs to produce a plan for

The implementation plan must cover, in deploy order:

1. **Render the two title PNGs** via the existing CDP script. Commit to `ahavah-web/public/email/`.
2. **Land migration 0024** (see parent spec) — adds `beta_signup.referral_code`, `referral_intro_sent_at`, plus the `referral` table.
3. **Land `service/referrals/__init__.py`** with `mint_code()` (needed by the email CLI's backfill phase before any sends fire).
4. **Land `emails/referral_intro.py`** with the template above.
5. **Land `emails/send_referral_intro.py`** CLI mirroring `send_beta_launch.py` — backfill missing codes, skip suppressed/unsubscribed/already-sent, mark `referral_intro_sent_at = NOW()` per send.
6. **Manual smoke test sequence** before real blast:
   - `python -m emails.send_referral_intro` (dry run, lists codes)
   - `python -m emails.send_referral_intro --only e2e@techbaseltd.com` — proves render + suppression filter
   - Visual review of the rendered email in a real Gmail inbox
7. **Real blast**: `python -m emails.send_referral_intro --all` once user confirms the test render looks right.

(Items 8 onward — `/i/[code]` route, `/share/<code>` page, `<ReferralCard>`, attribution + credit hooks — belong to the broader referrals plan from the parent spec, not the email-specific subset.)
