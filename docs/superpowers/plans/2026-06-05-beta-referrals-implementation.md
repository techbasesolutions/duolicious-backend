# Beta-tester referrals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the beta-tester referral system end-to-end: each beta opt-in gets a personal `ahavah.app/i/<code>` link, a one-shot email blast goes to the 15 current cohort members, signups via the link are recorded immediately, and 5 tokens are credited per successful referral at launch when both inviter and invitee finish onboarding.

**Architecture:** Two-phase build. Phase 1 (capture + email blast, ship in 1–2 days) lands the data model + attribution recording + email blast — minted-but-uncredited rows accumulate in the DB until Phase 2 fires. Phase 2 (credit firing, can land any time before/at launch) wires `_credit_one()` into `POST /finish-onboarding` for both inviter and invitee paths, then optionally adds the in-app surfaces (`GET /referrals/me`, `<ReferralCard>`). The strict abuse gate ("both sides must graduate") means we cannot fire credits pre-launch anyway, so the phase split has no functional cost.

**Tech Stack:** Backend — Python 3.12, sync psycopg via `service.database.api_tx()`, Flask routes via `service.api.decorators.{get,post,apost}`, Pydantic v2 via `duotypes`, existing `emails.base` shell + `service.tokens.credit()` + `service.unsubscribe.make_url()`. Frontend — Next.js 16 (App Router, Turbopack), React 19, Tailwind v4, existing `apiClient` from `src/lib/api-client`. Email — existing `aws_smtp` HTTPS path to Resend.

**Specs consumed:**
- [`docs/superpowers/specs/2026-06-05-beta-referrals-design.md`](../specs/2026-06-05-beta-referrals-design.md) — parent spec
- [`docs/superpowers/specs/2026-06-05-referral-intro-email-design.md`](../specs/2026-06-05-referral-intro-email-design.md) — email companion

**Deploy mechanics (background):**
- `ahavah-api` push to `ahavah/main` → GHA `Deploy ahavah/main → droplet` → applies `migrations/*.sql` via `psql -f` → rebuilds + force-recreates `api chat cron` containers → health check
- `ahavah-web` push to `master` → Vercel auto-deploy to production. For this plan's pre-launch verification we deploy to the stable preview alias `ahavah-preview.vercel.app` via `vercel deploy --token <token>` + `vercel alias set <new-url> ahavah-preview.vercel.app`.

---

## File structure

### `ahavah-api` (backend)

| File | Action | Purpose |
|---|---|---|
| `migrations/0024_referrals.sql` | Create | Schema: `beta_signup.referral_code` + `referral_intro_sent_at` + `referral` table + extend `token_ledger_reason_check` |
| `service/referrals/__init__.py` | Create | Public module surface: `mint_code()`, `attribute()`, `credit_pending_for_invitee()`, `credit_pending_for_inviter()`, `get_my_stats()`, internal `_credit_one()`. SQL inlined (matches `service/beta/__init__.py` pattern) |
| `service/api/referrals_routes.py` | Create | New routes `POST /referrals/code` (public) + `GET /referrals/me` (authed). Imported at the bottom of `service/api/__init__.py` like `beta_routes.py` |
| `service/api/__init__.py` | Modify | Add `import service.api.referrals_routes` at the bottom (matches `beta_routes` import on line 1002) |
| `duotypes/__init__.py` | Modify | Add `inviter_code` field to `PostBetaTester` + `PostRequestOtp` |
| `service/api/beta_routes.py` | Modify | After `register_beta()` returns `is_new=True`, call `referrals.attribute(tx, req.inviter_code, req.email)` in same tx |
| `service/person/__init__.py` (`post_request_otp`) | Modify | After successful `Q_INSERT_DUO_SESSION`, call `referrals.attribute(tx, req.inviter_code, req.email)` only for brand-new sessions |
| `service/person/__init__.py` (`post_finish_onboarding`) | Modify (Phase 2) | After `Q_FINISH_ONBOARDING`, call `credit_pending_for_invitee()` + `credit_pending_for_inviter()` in same tx |
| `emails/referral_intro.py` | Create | Email template per companion spec |
| `emails/send_referral_intro.py` | Create | CLI with `--only / --all` matching `send_beta_launch.py` pattern, includes backfill phase |
| `tests/test_referrals.py` | Create | Unit tests for `mint_code()` alphabet + collision retry + `attribute()` self-referral + idempotency |
| `scripts/render-title-png.mjs` | Create (if not present) | Node CDP script to render the two PNGs from inline HTML |

### `ahavah-web` (frontend)

| File | Action | Purpose |
|---|---|---|
| `public/email/title-referral.png` | Create | Light variant of the Ultra title image (rendered by the Node script) |
| `public/email/title-referral-wht.png` | Create | Dark variant of the Ultra title image |
| `src/lib/storage-keys.ts` | Modify | Add `REFERRAL_CODE_KEY = "ahavah.ref"` constant |
| `src/lib/referrals.ts` | Create | `readReferralCode()`, `clearReferralCode()`, `getReferralCode(email)`, `getMyReferralStats()` |
| `src/lib/waitlist.ts` | Modify | `postWaitlist` merges `inviter_code: readReferralCode()` into body |
| `src/lib/beta.ts` | Modify | `registerBetaTester` merges `inviter_code: readReferralCode()` into body |
| `src/lib/auth-otp.ts` | Modify | `requestEmailOtp` merges `inviter_code: readReferralCode()` into body |
| `src/app/i/[code]/page.tsx` | Create | Server component: validate code regex, set `ahavah.ref` cookie, redirect to `/waitlist` (pre-launch) or `/auth/sign-up` (post-launch) |
| `src/app/share/[code]/page.tsx` | Create | Client component: triggers Web Share API if available, else shows copy-to-clipboard button |
| `src/components/app/referral-card.tsx` | Create (Phase 2 — optional v1 ship) | Sibling of `<BetaTesterCard>` on `/waitlist` completion screen |

---

## Phase 1 — Capture + email blast

Target: shippable in 1–2 days. After Phase 1, the email is in the cohort's inboxes, signups via the link are recorded in the `referral` table, and the credits are accumulating as `pending` / `graduated` rows waiting for Phase 2.

### Task 1: Migration 0024 (schema)

**Files:**
- Create: `ahavah-api/migrations/0024_referrals.sql`

- [ ] **Step 1: Create the migration file**

Path: `ahavah-api/migrations/0024_referrals.sql`

```sql
-- 0024_referrals.sql
-- Beta-tester referral system: per-tester reusable code, attribution
-- row per invitee, extended token_ledger reason enum. Idempotent.
-- See docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
BEGIN;

ALTER TABLE beta_signup
  ADD COLUMN IF NOT EXISTS referral_code           TEXT,
  ADD COLUMN IF NOT EXISTS referral_intro_sent_at  TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS beta_signup_referral_code_uidx
  ON beta_signup (referral_code)
  WHERE referral_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS referral (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  inviter_email   TEXT NOT NULL REFERENCES beta_signup(email) ON DELETE CASCADE,
  invitee_email   TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('pending','graduated','credited'))
                  DEFAULT 'pending',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  graduated_at    TIMESTAMPTZ,
  credited_at     TIMESTAMPTZ,
  UNIQUE (invitee_email),
  CHECK (inviter_email <> invitee_email)
);

CREATE INDEX IF NOT EXISTS referral_inviter_status_idx
  ON referral (inviter_email, status);

CREATE INDEX IF NOT EXISTS referral_status_idx
  ON referral (status)
  WHERE status IN ('pending','graduated');

-- Extend token_ledger.reason enum to allow 'referral'. The constraint
-- name `token_ledger_reason_check` is the live name (verified via
-- `SELECT conname FROM pg_constraint WHERE conrelid = 'token_ledger'::regclass`).
ALTER TABLE token_ledger
  DROP CONSTRAINT IF EXISTS token_ledger_reason_check;

ALTER TABLE token_ledger
  ADD CONSTRAINT token_ledger_reason_check
  CHECK (reason IN (
    'purchase','subscription_stipend','reveal_liker','super_like',
    'day_pass','boost','refund','referral'
  ));

COMMIT;
```

- [ ] **Step 2: Local syntax check (offline)**

Run from `ahavah-api/`:

```bash
grep -c "^BEGIN;\|^COMMIT;" migrations/0024_referrals.sql
```

Expected: `2`

- [ ] **Step 3: Commit**

```bash
cd ahavah-api
git add migrations/0024_referrals.sql
git commit -m "migration 0024: referrals schema + extend token_ledger enum

Adds beta_signup.referral_code + referral_intro_sent_at, referral
table keyed on invitee_email, and extends token_ledger_reason_check
to include 'referral'. Idempotent; safe to re-run on every deploy.

See docs/superpowers/specs/2026-06-05-beta-referrals-design.md."
```

---

### Task 2: `service/referrals/` module (mint_code + attribute only)

**Phase 1 ships the two functions that get exercised by signups now.** `credit_pending_for_*` + `get_my_stats()` land in Phase 2.

**Files:**
- Create: `ahavah-api/service/referrals/__init__.py`
- Create: `ahavah-api/tests/test_referrals.py`

- [ ] **Step 1: Write the failing unit tests**

Path: `ahavah-api/tests/test_referrals.py`

```python
"""Unit tests for service.referrals.

These exercise the pure-logic pieces (Crockford alphabet, normalization,
self-referral detection). The integration paths (attribute → row insert,
credit_pending_for_* → token_ledger row) are covered by a separate shell
smoke test against the live test stack."""
from __future__ import annotations

import pytest

from service.referrals import (
    _ALPHABET,
    _CODE_LENGTH,
    _normalize_email,
    _is_well_formed_code,
)


def test_alphabet_is_crockford_base32_without_ambiguous_chars():
    # No I, L, O, U (Crockford excludes them to avoid digit confusion)
    for forbidden in "ILOU":
        assert forbidden not in _ALPHABET
    # 32 chars exactly
    assert len(_ALPHABET) == 32
    # All caps + digits
    assert _ALPHABET == "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def test_code_length_is_seven():
    assert _CODE_LENGTH == 7


def test_normalize_email_strips_and_lowercases():
    assert _normalize_email("  Foo@BAR.COM  ") == "foo@bar.com"
    assert _normalize_email("") == ""
    assert _normalize_email(None) == ""


def test_is_well_formed_code_accepts_valid_crockford():
    assert _is_well_formed_code("0123ABC") is True
    assert _is_well_formed_code("ZZZZZZZ") is True


def test_is_well_formed_code_rejects_garbage():
    # Wrong length
    assert _is_well_formed_code("ABC") is False
    assert _is_well_formed_code("ABCDEFGH") is False
    # Forbidden Crockford letters
    assert _is_well_formed_code("0123ILU") is False
    # Lowercase (codes are normalized to upper at mint time; the route
    # accepts case-insensitive, but the well-formed check is strict)
    assert _is_well_formed_code("0123abc") is False
    # Symbols
    assert _is_well_formed_code("01-23AB") is False
    # Empty / None
    assert _is_well_formed_code("") is False
    assert _is_well_formed_code(None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ahavah-api && python -m pytest tests/test_referrals.py -v`
Expected: `ImportError: cannot import name '_ALPHABET' from 'service.referrals'` (module doesn't exist yet)

- [ ] **Step 3: Create the module with the minimal surface needed for Phase 1**

Path: `ahavah-api/service/referrals/__init__.py`

```python
"""Beta-tester referrals — Phase 1 surface.

Functions in this file are called from POST /beta-tester and POST
/request-otp to record attribution when a new signup arrives via a
`/i/<code>` link. Credit firing (credit_pending_for_{invitee,inviter}
+ _credit_one) lands in Phase 2 alongside the post_finish_onboarding
integration; the parent spec at
docs/superpowers/specs/2026-06-05-beta-referrals-design.md describes
the full surface.

Caller owns the api_tx; everything below takes a psycopg cursor `tx`
as the first arg and never opens its own transaction. This matches
the existing service.beta / service.waitlist convention."""
from __future__ import annotations

import secrets
from typing import Optional

# Crockford base32 — no I, L, O, U (eliminates digit-letter ambiguity
# when read aloud or scribbled on paper). 32^7 = 34 billion codes.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 7
_MAX_MINT_RETRIES = 3


class ReferralCodeCollision(Exception):
    """Raised after _MAX_MINT_RETRIES consecutive unique-violation
    failures minting a code. Should never happen at 32^7 = 34B keyspace
    with only ~15 codes in flight; operational signal that the alphabet
    or length needs bumping."""


def _normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _is_well_formed_code(code: Optional[str]) -> bool:
    if not code or not isinstance(code, str):
        return False
    if len(code) != _CODE_LENGTH:
        return False
    return all(c in _ALPHABET for c in code)


def _random_code() -> str:
    # secrets.choice for cryptographic randomness; not strictly required
    # for a public-facing share code, but cheap and aligns with the rest
    # of the codebase (session tokens, OTPs).
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


_Q_GET_EXISTING_CODE = """
    SELECT referral_code FROM beta_signup WHERE email = %(email)s
"""

_Q_SET_CODE = """
    UPDATE beta_signup
       SET referral_code = %(code)s
     WHERE email = %(email)s
       AND referral_code IS NULL
"""

_Q_INVITER_FROM_CODE = """
    SELECT email FROM beta_signup WHERE referral_code = %(code)s
"""

_Q_INSERT_REFERRAL = """
    INSERT INTO referral (inviter_email, invitee_email)
    VALUES (%(inviter_email)s, %(invitee_email)s)
    ON CONFLICT (invitee_email) DO NOTHING
    RETURNING id, inviter_email, invitee_email, status
"""


def mint_code(tx, email: str) -> Optional[str]:
    """Idempotent. Returns the beta tester's referral_code, generating
    + storing one if NULL. Returns None if the email isn't in
    beta_signup (caller decides what to do)."""
    norm = _normalize_email(email)
    row = tx.execute(_Q_GET_EXISTING_CODE, dict(email=norm)).fetchone()
    if row is None:
        return None  # not a beta tester
    if row["referral_code"]:
        return row["referral_code"]

    last_err: Optional[Exception] = None
    for _ in range(_MAX_MINT_RETRIES):
        code = _random_code()
        try:
            cur = tx.execute(_Q_SET_CODE, dict(email=norm, code=code))
            if cur.rowcount == 1:
                return code
            # rowcount=0 means someone else minted a code for this row
            # between our SELECT and UPDATE; re-read.
            row2 = tx.execute(_Q_GET_EXISTING_CODE, dict(email=norm)).fetchone()
            if row2 and row2["referral_code"]:
                return row2["referral_code"]
        except Exception as e:
            # Unique-violation on the partial index (rare); retry with
            # a fresh random code.
            last_err = e
    raise ReferralCodeCollision(
        f"could not mint referral_code for {norm!r} after "
        f"{_MAX_MINT_RETRIES} tries; last error: {last_err!r}"
    )


def attribute(
    tx,
    inviter_code: Optional[str],
    invitee_email: str,
) -> Optional[dict]:
    """Called when a new signup arrives via /i/<code>.

    - Returns None if inviter_code is None / malformed / not found.
    - Returns None if inviter_email == invitee_email (self-referral).
    - Else INSERTs a referral row ON CONFLICT (invitee_email) DO NOTHING.
      Returns the row dict iff inserted, None if invitee was already
      attributed to someone else (first attribution wins).

    Safe to call multiple times for the same invitee (idempotent via the
    UNIQUE constraint). Safe to call with bad inputs (returns None)."""
    if not _is_well_formed_code(inviter_code):
        return None

    invitee_norm = _normalize_email(invitee_email)
    if not invitee_norm:
        return None

    inviter_row = tx.execute(
        _Q_INVITER_FROM_CODE, dict(code=inviter_code)
    ).fetchone()
    if inviter_row is None:
        return None  # unknown code
    inviter_email = inviter_row["email"]

    if inviter_email == invitee_norm:
        return None  # self-referral, silently void

    row = tx.execute(
        _Q_INSERT_REFERRAL,
        dict(inviter_email=inviter_email, invitee_email=invitee_norm),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `cd ahavah-api && python -m pytest tests/test_referrals.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ahavah-api
git add service/referrals/__init__.py tests/test_referrals.py
git commit -m "service/referrals: mint_code + attribute (Phase 1 surface)

Pure-logic helpers (Crockford alphabet, normalization, well-formed
check) covered by tests/test_referrals.py. Integration paths
(attribute → row insert) covered by the live smoke test later in the
plan.

Caller owns the api_tx; callable signatures match service.beta and
service.waitlist conventions. Credit functions land in Phase 2.

See docs/superpowers/specs/2026-06-05-beta-referrals-design.md."
```

---

### Task 3: Extend `PostBetaTester` + `PostRequestOtp` with `inviter_code`

**Files:**
- Modify: `ahavah-api/duotypes/__init__.py`

- [ ] **Step 1: Find the existing `PostBetaTester` model in `duotypes/__init__.py`**

Run: `grep -n "class PostBetaTester\|class PostRequestOtp" ahavah-api/duotypes/__init__.py`

Expected: two line numbers — note them. (Today: `PostRequestOtp` ~line 259, `PostBetaTester` ~line 352.)

- [ ] **Step 2: Add `inviter_code` field to `PostRequestOtp`**

In `ahavah-api/duotypes/__init__.py`, inside `class PostRequestOtp(BaseModel)`, add after the existing `turnstile_token` field:

```python
    # Referral code from /i/<code> landing route, surfaced in
    # localStorage and forwarded by the FE on every public POST that
    # might create a new account. Crockford base32, 7 chars, optional.
    # See docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
    inviter_code: Optional[str] = Field(
        default=None,
        min_length=7,
        max_length=7,
        pattern=r"^[0-9A-HJ-NP-TV-Z]+$",  # Crockford: no I/L/O/U
    )
```

- [ ] **Step 3: Add the same field to `PostBetaTester`**

In `class PostBetaTester(BaseModel)`, after the existing `turnstile_token`:

```python
    # See PostRequestOtp.inviter_code docstring.
    inviter_code: Optional[str] = Field(
        default=None,
        min_length=7,
        max_length=7,
        pattern=r"^[0-9A-HJ-NP-TV-Z]+$",
    )
```

- [ ] **Step 4: Run mypy + a quick import check**

```bash
cd ahavah-api
./mypy.sh 2>&1 | tail -5
python -c "from duotypes import PostBetaTester, PostRequestOtp; \
print(PostBetaTester(email='x@y.com', inviter_code='ABCDEFG'))"
```

Expected: mypy clean, the print outputs a populated model. If `inviter_code` is rejected as not Crockford, double-check the pattern doesn't include `I/L/O/U`.

- [ ] **Step 5: Commit**

```bash
cd ahavah-api
git add duotypes/__init__.py
git commit -m "duotypes: add inviter_code field to PostBetaTester + PostRequestOtp

Optional Crockford base32 7-char field, forwarded by the FE from
localStorage when the user signed up via /i/<code>. Same additive
pattern as honeypot + turnstile_token."
```

---

### Task 4: Wire `attribute()` into `POST /beta-tester`

**Files:**
- Modify: `ahavah-api/service/api/beta_routes.py`

- [ ] **Step 1: Add the `referrals` import**

In `ahavah-api/service/api/beta_routes.py`, at the top of the existing imports block:

```python
from service.referrals import attribute as attribute_referral
```

- [ ] **Step 2: Call `attribute()` after `register_beta()` inside the existing tx**

Find the existing block in `post_beta_tester`:

```python
    with api_tx() as tx:
        is_new = register_beta(tx, req.email, None)
        total = beta_count(tx)
```

Replace with:

```python
    with api_tx() as tx:
        is_new = register_beta(tx, req.email, None)
        total = beta_count(tx)
        # Record referral attribution if the FE carried an inviter_code
        # from /i/<code>. Best-effort — bad codes / self-referrals /
        # already-attributed invitees return None and we move on without
        # affecting the beta-tester flow. See parent spec.
        if is_new:
            attribute_referral(tx, req.inviter_code, req.email)
```

- [ ] **Step 3: Smoke test plan (run after Phase 1 deploys, not now)**

This task ships untested in isolation. Verification fires at Task 13 via curl against the live preview.

- [ ] **Step 4: Commit**

```bash
cd ahavah-api
git add service/api/beta_routes.py
git commit -m "beta_routes: record referral attribution on new beta opt-ins

After register_beta() returns is_new=True, call
service.referrals.attribute(tx, req.inviter_code, req.email) inside
the same api_tx so the beta_signup INSERT and referral row INSERT
commit atomically. Bad codes / self-referrals return None silently."
```

---

### Task 5: Wire `attribute()` into `POST /request-otp`

This is the post-launch attribution path. We wire it now (it's safe — pre-launch the inviter_code field is just ignored if no row reaches the bottom of post_request_otp) so we don't need a follow-up deploy at launch.

**Files:**
- Modify: `ahavah-api/service/person/__init__.py` (`post_request_otp`)

- [ ] **Step 1: Locate `post_request_otp`**

Run: `grep -n "def post_request_otp" ahavah-api/service/person/__init__.py`

- [ ] **Step 2: Add the referrals import at the top of the file**

In the existing imports block, add:

```python
from service.referrals import attribute as attribute_referral
```

- [ ] **Step 3: Call `attribute()` after Q_INSERT_DUO_SESSION returns a fresh row**

Find the existing block in `post_request_otp`:

```python
    with api_tx() as tx:
        # Purge any stale UNSIGNED-IN sessions for this email first ...
        tx.execute(
            Q_PURGE_STALE_UNSIGNED_SESSIONS,
            dict(email=req.email),
        )
        rows = tx.execute(Q_INSERT_DUO_SESSION, params).fetchall()
```

Add immediately after the `rows = ...` line, before the `try / except` block that handles the otp extraction:

```python
        # Record referral attribution if the FE carried an inviter_code
        # from /i/<code>. Best-effort — bad codes / self-referrals /
        # already-attributed invitees return None silently. Same tx so
        # any later failure rolls this back too. See parent spec.
        attribute_referral(tx, getattr(req, "inviter_code", None), req.email)
```

- [ ] **Step 4: mypy + import check**

```bash
cd ahavah-api
./mypy.sh 2>&1 | tail -5
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd ahavah-api
git add service/person/__init__.py
git commit -m "post_request_otp: record referral attribution on new sessions

Calls service.referrals.attribute(tx, req.inviter_code, req.email)
inside the post_request_otp tx, after the duo_session row is created.
Safe pre-launch (inviter_code is ignored if not present) and active
post-launch when /auth/sign-up is the entry point."
```

---

### Task 6: Render the title PNGs (`title-referral.png` + `title-referral-wht.png`)

The CDP-rendered Ultra title images are required for the email template's `title_image()` call. Render them once locally + commit to `ahavah-web/public/email/`.

**Files:**
- Create: `ahavah-api/scripts/render-title-png.mjs` (if not already present)
- Create: `ahavah-web/public/email/title-referral.png`
- Create: `ahavah-web/public/email/title-referral-wht.png`

- [ ] **Step 1: Check if a render script exists from prior title work**

Run: `ls ahavah-api/scripts/render-title-png.mjs ahavah-api/scripts/render-title.mjs 2>&1`

If a script exists, skip to Step 3. Otherwise continue.

- [ ] **Step 2: Create the render script**

Path: `ahavah-api/scripts/render-title-png.mjs`

```js
// Render a single Ultra-typeface title image to PNG via headless Chrome
// + CDP. Used for emails/*.py title_image() asset pairs. Usage:
//
//   node scripts/render-title-png.mjs \
//     --text "Bring someone with you" \
//     --color "#0F0B1F" \
//     --accent "#BC96FF" \
//     --out "../ahavah-web/public/email/title-referral.png"
//
// The accent color is applied to the trailing period (matches the
// family convention). Width 1056 (= 528 * 2x raster). Transparent bg.

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith("--")) acc.push([cur.slice(2), arr[i + 1]]);
    return acc;
  }, []),
);
if (!args.text || !args.color || !args.out) {
  console.error("usage: --text TEXT --color #HEX --accent #HEX --out PATH");
  process.exit(2);
}
const ACCENT = args.accent || args.color;
const TEXT = args.text;
const COLOR = args.color;
const OUT = resolve(args.out);
const WIDTH = 1056;

const CHROME_CANDIDATES = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];
const BROWSER = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!BROWSER) {
  console.error("No Chrome/Edge found");
  process.exit(2);
}

const html = `<!doctype html><html><head><meta charset="utf-8"/>
<link href="https://fonts.googleapis.com/css2?family=Ultra&display=block" rel="stylesheet"/>
<style>
html,body{margin:0;padding:0;background:transparent;}
#root{display:inline-block;font-family:'Ultra',serif;font-size:92px;
       line-height:1.0;color:${COLOR};padding:24px;white-space:nowrap;}
.acc{color:${ACCENT};}
</style></head>
<body><div id="root">${TEXT.replace(/\.$/, "")}<span class="acc">.</span></div>
<script>window.__ready=false;document.fonts.ready.then(()=>{window.__ready=true});</script>
</body></html>`;

const profile = mkdtempSync(join(tmpdir(), "render-title-"));
const PORT = 9444;
const child = spawn(
  BROWSER,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--hide-scrollbars",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    `--window-size=${WIDTH},400`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

async function waitForCdp() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return (await r.json()).webSocketDebuggerUrl;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("CDP did not start");
}

const wsUrl = await waitForCdp();
const ws = new WebSocket(wsUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
const events = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id != null) {
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
  } else if (m.method) {
    (events.get(m.method) || []).forEach((cb) => cb(m.params));
  }
});
const send = (method, params = {}, sessionId) => {
  const i = ++id;
  return new Promise((res, rej) => {
    pending.set(i, { resolve: res, reject: rej });
    ws.send(JSON.stringify({ id: i, method, params, sessionId }));
  });
};
const on = (m, cb) => {
  if (!events.has(m)) events.set(m, []);
  events.get(m).push(cb);
};

const { targetInfos } = await send("Target.getTargets");
const t = targetInfos.find((x) => x.type === "page");
const { sessionId } = await send("Target.attachToTarget", {
  targetId: t.targetId,
  flatten: true,
});
const s = (m, p) => send(m, p, sessionId);

await s("Page.enable");
await s("Runtime.enable");
await s("Emulation.setDefaultBackgroundColorOverride", {
  color: { r: 0, g: 0, b: 0, a: 0 },
});
await s("Page.navigate", { url: "data:text/html;base64," + Buffer.from(html).toString("base64") });
await new Promise((r) => on("Page.loadEventFired", r));
// Wait for the Ultra webfont to load
for (let i = 0; i < 40; i++) {
  const r = await s("Runtime.evaluate", { expression: "window.__ready === true" });
  if (r.result && r.result.value === true) break;
  await new Promise((r) => setTimeout(r, 100));
}

// Get the #root bounding box
const box = await s("Runtime.evaluate", {
  expression:
    "(function(){var b=document.getElementById('root').getBoundingClientRect();return JSON.stringify({x:b.left,y:b.top,w:b.width,h:b.height});})()",
});
const { x, y, w, h } = JSON.parse(box.result.value);

const shot = await s("Page.captureScreenshot", {
  format: "png",
  clip: { x, y, width: w, height: h, scale: 1 },
});
writeFileSync(OUT, Buffer.from(shot.data, "base64"));
console.error(`saved ${OUT} (${Math.round(w)}x${Math.round(h)})`);

child.kill();
ws.close();
process.exit(0);
```

- [ ] **Step 3: Render the light variant**

Run from `ahavah-api/`:

```bash
node scripts/render-title-png.mjs \
  --text "Bring someone with you." \
  --color "#0F0B1F" \
  --accent "#BC96FF" \
  --out "../ahavah-web/public/email/title-referral.png"
```

Expected stderr: `saved /…/title-referral.png (~880x~140)` (exact pixels depend on Ultra metrics).

- [ ] **Step 4: Render the dark variant**

```bash
node scripts/render-title-png.mjs \
  --text "Bring someone with you." \
  --color "#FFFFFF" \
  --accent "#BC96FF" \
  --out "../ahavah-web/public/email/title-referral-wht.png"
```

Expected stderr: another `saved` line.

- [ ] **Step 5: Visual sanity check**

Open both PNGs in any image viewer. Both should show "Bring someone with you" in heavy Ultra typeface, the trailing period in lavender. The light variant has ink-color text; the dark variant has white text. Background must be transparent (open in something that shows transparency as a checker pattern, e.g. paint.net, Photos, Preview).

- [ ] **Step 6: Commit PNGs to ahavah-web; commit script to ahavah-api**

```bash
cd ahavah-api
git add scripts/render-title-png.mjs
git commit -m "scripts: CDP title-image PNG renderer (reusable for email family)"

cd ../ahavah-web
git add public/email/title-referral.png public/email/title-referral-wht.png
git commit -m "email assets: title-referral{,-wht}.png for referral_intro template

Rendered via ahavah-api/scripts/render-title-png.mjs at 1056px width
(2x raster of the 528px email-rendered width). Ultra typeface, INK
on light + white on dark, trailing period in LAVENDER. Source spec:
docs/superpowers/specs/2026-06-05-referral-intro-email-design.md."
```

---

### Task 7: `emails/referral_intro.py` template

**Files:**
- Create: `ahavah-api/emails/referral_intro.py`

- [ ] **Step 1: Create the template module**

Path: `ahavah-api/emails/referral_intro.py` — copy verbatim from the companion spec's "Template module skeleton" section:

```python
"""Referral-intro email — one-shot blast to the beta cohort.

Sends each current beta_signup their personal referral link plus the
reward framing. Inline-styled via emails.base; dark-mode + suppression
+ unsubscribe all handled by the family conventions. Send is gated by
emails.send_referral_intro CLI (not fired by any route)."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN, WEB_BASE_URL
from emails.base import (
    render,
    button,
    chip,
    callout,
    title_image,
    is_suppressed_send,
    INK_SOFT,
    INDIGO,
    MUTED,
    SANS,
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
You're receiving this because you opted into the Ahavah beta at
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
    """Synchronous send. Skips suppressed addresses (example.com /
    techbaseltd.com). Best-effort (aws_smtp retries then gives up
    without raising)."""
    if is_suppressed_send(email):
        return
    from smtp import aws_smtp
    unsub = _unsub_url("beta", email, WEB_BASE_URL)
    aws_smtp.send(
        subject=SUBJECT,
        body=referral_intro_html(email, code),
        to_addr=email,
        from_addr=FROM_ADDR,
        list_unsubscribe=(
            f"<mailto:admin@ahavah.app?subject=Unsubscribe>, <{unsub}>"
        ),
    )
```

- [ ] **Step 2: Render-only smoke (no SMTP) to make sure HTML compiles**

```bash
cd ahavah-api
python -c "from emails.referral_intro import referral_intro_html; \
print(len(referral_intro_html('test@example.com', 'ABCDEFG')))"
```

Expected: a positive integer (the rendered HTML length, typically 6000-9000 chars).

- [ ] **Step 3: Commit**

```bash
cd ahavah-api
git add emails/referral_intro.py
git commit -m "emails: referral_intro template (one-shot blast to beta cohort)

Per docs/superpowers/specs/2026-06-05-referral-intro-email-design.md.
Reuses the existing emails.base shell + unsubscribe + suppression
list. Two URLs: /share/<code> (inviter's action surface, Web Share
API on click) and /i/<code> (the actual invitee landing, embedded as
plaintext for copy/paste / forward / screenshot scenarios).

Send is fired exclusively by emails/send_referral_intro CLI, not
from any route."
```

---

### Task 8: `emails/send_referral_intro.py` CLI

**Files:**
- Create: `ahavah-api/emails/send_referral_intro.py`

- [ ] **Step 1: Create the CLI**

Path: `ahavah-api/emails/send_referral_intro.py`

```python
"""One-off: blast the referral-intro email to the beta cohort.

Run inside the api container on the droplet (needs DB + SMTP env):

    python -m emails.send_referral_intro                 # DRY RUN — lists recipients + codes
    python -m emails.send_referral_intro --only a@b.com  # send to one address (test)
    python -m emails.send_referral_intro --all           # blast every beta_signup row

Dry-run is the default so an accidental invocation never sends mail.
`--all` skips: suppressed addresses (example.com / techbaseltd.com),
unsubscribed addresses, and addresses that already received this
email (referral_intro_sent_at IS NOT NULL). Re-running is idempotent.

The backfill phase runs FIRST on every invocation (including --only)
so we never email someone whose row lacks a code yet."""
from __future__ import annotations

import argparse

from database import api_tx
from emails.base import is_suppressed_send
from emails.referral_intro import send_referral_intro, SUBJECT, FROM_ADDR
from service.referrals import mint_code


_Q_TARGETS = """
    SELECT email
      FROM beta_signup
     WHERE unsubscribed_at IS NULL
       AND referral_intro_sent_at IS NULL
     ORDER BY created_at
"""

_Q_GET_CODE = """
    SELECT referral_code FROM beta_signup WHERE email = %(email)s
"""

_Q_MARK_SENT = """
    UPDATE beta_signup
       SET referral_intro_sent_at = NOW()
     WHERE email = %(email)s
"""


def _backfill_and_target_codes() -> list[tuple[str, str]]:
    """Returns [(email, code), ...] for every row that still needs an
    email blast, with codes minted as needed. Run inside a single tx
    for atomicity — partial failure leaves rows un-coded but un-emailed
    too, which is the correct invariant."""
    out: list[tuple[str, str]] = []
    with api_tx() as tx:
        targets = tx.execute(_Q_TARGETS).fetchall()
        for r in targets:
            email = r["email"]
            code = mint_code(tx, email)
            if code is None:
                continue  # shouldn't happen — the SELECT proves the row exists
            out.append((email, code))
    return out


def _get_code(email: str) -> str | None:
    with api_tx() as tx:
        row = tx.execute(_Q_GET_CODE, dict(email=email.strip().lower())).fetchone()
        return row["referral_code"] if row else None


def _mark_sent(email: str) -> None:
    with api_tx() as tx:
        tx.execute(_Q_MARK_SENT, dict(email=email.strip().lower()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Blast the Ahavah referral-intro email.")
    ap.add_argument("--only", metavar="EMAIL", help="send to a single address (test)")
    ap.add_argument("--all", action="store_true", help="send to every beta_signup row that hasn't been emailed yet")
    args = ap.parse_args()

    print(f"Subject: {SUBJECT!r}  From: {FROM_ADDR!r}")

    if args.only:
        email = args.only.strip().lower()
        # Backfill the single row's code if needed.
        with api_tx() as tx:
            code = mint_code(tx, email)
        if code is None:
            print(f"FAIL: {email} is not in beta_signup; nothing minted, nothing sent.")
            return
        print(f"Sending single test to {email} (code={code}) ...")
        send_referral_intro(email, code)
        _mark_sent(email)
        print("done (check the inbox; aws_smtp is best-effort).")
        return

    targets = _backfill_and_target_codes()
    if not args.all:
        print(f"DRY RUN — {len(targets)} recipient(s) (suppressed addresses included for visibility):")
        for e, c in targets:
            suppressed = " [SUPPRESSED — skipped on --all]" if is_suppressed_send(e) else ""
            print(f"   - {e:40s} code={c}{suppressed}")
        print("\nRe-run with --only EMAIL to test one, or --all to send to everyone.")
        return

    sent = skipped = 0
    print(f"Sending to {len(targets)} candidate(s)...")
    for e, c in targets:
        if is_suppressed_send(e):
            print(f"   skip (suppressed) {e}")
            skipped += 1
            continue
        send_referral_intro(e, c)
        _mark_sent(e)
        print(f"   sent {e} (code={c})")
        sent += 1
    print(f"done — {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run locally to verify it parses + can connect to DB (will fail outside the container — that's fine, we just want the import path to be clean)**

```bash
cd ahavah-api
python -c "import emails.send_referral_intro; print('import ok')"
```

Expected: `import ok` printed, no traceback. The actual DB connection happens in the container.

- [ ] **Step 3: Commit**

```bash
cd ahavah-api
git add emails/send_referral_intro.py
git commit -m "emails: send_referral_intro CLI (one-shot blast)

Matches send_beta_launch pattern: --only / --all / dry-run default.
Two safety invariants the spec called for:

  1. Backfill (mint_code) runs FIRST on every invocation so the
     blast cannot send an email without a code.
  2. referral_intro_sent_at stamps atomically with each successful
     send, so re-running is idempotent (already-sent rows drop out
     of _Q_TARGETS)."
```

---

### Task 9: Frontend storage key + `referrals` lib

**Files:**
- Modify: `ahavah-web/src/lib/storage-keys.ts`
- Create: `ahavah-web/src/lib/referrals.ts`

- [ ] **Step 1: Add the storage key constant**

In `ahavah-web/src/lib/storage-keys.ts`, after the existing `MAP_FIRST_MOUNT_KEY` line, append:

```typescript
/**
 * Referral code captured by /i/[code]. Persisted in localStorage so it
 * survives the multi-step landing → /waitlist → /beta-tester flow, and
 * survives tab close (people often click links and come back later).
 * 90-day TTL enforced by the cookie set on the same route — localStorage
 * itself has no TTL, but the cookie's expiry implicitly invalidates.
 */
export const REFERRAL_CODE_KEY = "ahavah.ref";
```

- [ ] **Step 2: Create `src/lib/referrals.ts`**

Path: `ahavah-web/src/lib/referrals.ts`

```typescript
/**
 * Referral-code client helpers.
 *
 * The code arrives via /i/[code] which sets a 90-day `ahavah.ref` cookie
 * AND mirrors it to localStorage on mount (cookie for SSR / first-render
 * routing, localStorage for the long-lived stash that survives tab close).
 *
 * Read paths: postWaitlist / registerBetaTester / requestEmailOtp.
 * Clear paths: after a successful attribute() — the backend's INSERT-
 * ON-CONFLICT-DO-NOTHING semantics make re-attribution a no-op, but
 * clearing avoids the FE re-sending a code that's already been used.
 */
import { REFERRAL_CODE_KEY } from "@/lib/storage-keys";

const CROCKFORD_BASE32 = /^[0-9A-HJ-NP-TV-Z]{7}$/;

function isWellFormed(code: string | null | undefined): code is string {
  return !!code && CROCKFORD_BASE32.test(code);
}

export function readReferralCode(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(REFERRAL_CODE_KEY);
    return isWellFormed(v) ? v : null;
  } catch {
    return null;
  }
}

export function writeReferralCode(code: string): void {
  if (typeof window === "undefined") return;
  if (!isWellFormed(code)) return;
  try {
    window.localStorage.setItem(REFERRAL_CODE_KEY, code);
  } catch {
    // private mode / quota / disabled storage — silently skip
  }
}

export function clearReferralCode(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(REFERRAL_CODE_KEY);
  } catch {
    // ignore
  }
}
```

- [ ] **Step 3: Quick tsc-style sanity (no project tsc run needed; rely on the eslint pre-commit)**

The file will be linted on the next `git commit` via husky.

- [ ] **Step 4: Commit**

```bash
cd ahavah-web
git add src/lib/storage-keys.ts src/lib/referrals.ts
git commit -m "lib: referral-code helpers (read/write/clear from localStorage)

REFERRAL_CODE_KEY = 'ahavah.ref'. Crockford-base32 regex check on read
matches the backend Pydantic pattern so a tampered localStorage value
silently no-ops instead of being forwarded.

See docs/superpowers/specs/2026-06-05-beta-referrals-design.md
(frontend pieces section)."
```

---

### Task 10: `/i/[code]` landing route

**Files:**
- Create: `ahavah-web/src/app/i/[code]/page.tsx`

- [ ] **Step 1: Read Next 16 dynamic-route docs**

Per `ahavah-web/AGENTS.md`, Next 16 has breaking changes. Read the dynamic-route + cookies docs before writing code:

```bash
ls ahavah-web/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/
```

Confirm `cookies.md` and dynamic-route conventions are present; skim relevant sections.

- [ ] **Step 2: Create the route**

Path: `ahavah-web/src/app/i/[code]/page.tsx`

```typescript
/**
 * /i/[code] — referral-link landing.
 *
 * Validates the Crockford-base32 7-char code, sets a 90-day `ahavah.ref`
 * cookie (so SSR sees it on the destination route), and redirects to:
 *   - /waitlist   when NEXT_PUBLIC_PRELAUNCH != "false" (current pre-launch posture)
 *   - /auth/sign-up otherwise
 *
 * The same cookie value is mirrored to localStorage by a tiny mount-time
 * effect on /waitlist and /auth/sign-up, so subsequent reads everywhere
 * else in the FE go through `readReferralCode()` synchronously.
 *
 * Note: this is a SERVER component. `cookies()` from `next/headers`
 * works only here, not on the client.
 *
 * See docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
 */

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { REFERRAL_CODE_KEY } from "@/lib/storage-keys";

const CROCKFORD_BASE32 = /^[0-9A-HJ-NP-TV-Z]{7}$/;
const COOKIE_MAX_AGE_SEC = 60 * 60 * 24 * 90; // 90 days

export const dynamic = "force-dynamic";

type Params = Promise<{ code: string }>;

export default async function ReferralLanding({ params }: { params: Params }) {
  const { code } = await params;

  // Always-canonical-uppercase normalization; backend regex is upper-only.
  const normalized = (code || "").toUpperCase();

  if (CROCKFORD_BASE32.test(normalized)) {
    const jar = await cookies();
    jar.set(REFERRAL_CODE_KEY, normalized, {
      maxAge: COOKIE_MAX_AGE_SEC,
      sameSite: "lax",
      path: "/",
      httpOnly: false, // read by client mirror effect
    });
  }
  // Bad codes fall through silently — landing still redirects so the
  // user isn't shown an error page.

  const prelaunch = process.env.NEXT_PUBLIC_PRELAUNCH !== "false";
  redirect(prelaunch ? "/waitlist" : "/auth/sign-up");
}
```

- [ ] **Step 3: Add the cookie-to-localStorage mirror on `/waitlist` mount**

In `ahavah-web/src/app/waitlist/page.tsx`, find the existing `useEffect` block that handles `params` / `sessionStorage`, and add a sibling effect (anywhere near the top of the component):

```typescript
  // Mirror the referral cookie set by /i/[code] into localStorage so
  // postWaitlist + registerBetaTester can read it from one source.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const m = document.cookie.match(/(?:^|; )ahavah\.ref=([^;]+)/);
    if (m && /^[0-9A-HJ-NP-TV-Z]{7}$/.test(m[1])) {
      try { window.localStorage.setItem("ahavah.ref", m[1]); } catch {}
    }
  }, []);
```

- [ ] **Step 4: Add the same mirror effect on `/auth/sign-up`**

Same snippet inside the `SignUpPage` function body's useEffect block in `ahavah-web/src/app/auth/sign-up/page.tsx`.

- [ ] **Step 5: Commit**

```bash
cd ahavah-web
git add src/app/i src/app/waitlist/page.tsx src/app/auth/sign-up/page.tsx
git commit -m "app/i/[code]: referral landing route + cookie-to-localStorage mirror

- /i/[code] server component sets 90-day ahavah.ref cookie + redirects
  to /waitlist (pre-launch) or /auth/sign-up (post-launch).
- /waitlist + /auth/sign-up mount-time effect copies the cookie into
  localStorage so the rest of the FE has one source of truth.

Bad codes fall through silently; the user still lands on the correct
destination. See parent spec."
```

---

### Task 11: `/share/[code]` page

**Files:**
- Create: `ahavah-web/src/app/share/[code]/page.tsx`

- [ ] **Step 1: Create the page**

Path: `ahavah-web/src/app/share/[code]/page.tsx`

```typescript
/**
 * /share/[code] — the inviter's action surface.
 *
 * The referral-intro email's lime CTA button points here (NOT at /i/<code>
 * which would route the inviter to their own invite landing as if they
 * were an invitee). Triggers Web Share API on mobile; falls back to a
 * copy-to-clipboard button on desktop. Server component for the markup
 * + a small client island for the share/copy interactions.
 *
 * The plaintext URL the inviter actually shares is the /i/<code> URL,
 * NOT this page's URL. The email body also includes that URL as
 * copyable text right beneath the button so users who never click
 * through still have what they need.
 */
"use client";

import { useEffect, useState } from "react";
import { Copy, Check, Share2 } from "lucide-react";

import { Button } from "@/components/ui/button";

const CROCKFORD_BASE32 = /^[0-9A-HJ-NP-TV-Z]{7}$/;

export default function SharePage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const [code, setCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void params.then(({ code }) => {
      const normalized = (code || "").toUpperCase();
      if (CROCKFORD_BASE32.test(normalized)) setCode(normalized);
    });
  }, [params]);

  if (!code) {
    return (
      <main className="min-h-dvh grid place-items-center p-6 text-(--ink-2)">
        <p className="text-body">That link isn’t valid.</p>
      </main>
    );
  }

  const inviteUrl = `https://ahavah.app/i/${code}`;
  const shareText =
    "I’m on the Ahavah beta — Torah-observant matchmaking for serious believers. Use my link to join the beta: ";

  const handleShare = async () => {
    if (typeof navigator !== "undefined" && "share" in navigator) {
      try {
        await navigator.share({
          title: "Join me on Ahavah",
          text: shareText,
          url: inviteUrl,
        });
        return;
      } catch {
        // user cancelled — fall through to copy
      }
    }
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <main className="min-h-dvh bg-(--app) grid place-items-center p-6">
      <div className="w-full max-w-sm flex flex-col gap-5 text-center">
        <h1 className="text-display text-(--ink)">
          Your invite link<span className="text-(--color-lime)">.</span>
        </h1>
        <p className="text-body text-(--ink-2)">
          Tap below to share it via Messages, WhatsApp, or anywhere else.
        </p>
        <code className="block break-all rounded-2xl bg-(--card) p-4 text-meta text-(--ink) ring-1 ring-(--hairline)">
          {inviteUrl}
        </code>
        <Button size="cta" tone="cta" className="w-full" onClick={handleShare}>
          {copied ? (
            <>
              <Check />
              Copied
            </>
          ) : (
            <>
              <Share2 />
              Share your link
            </>
          )}
        </Button>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Manual smoke (later, after deploy): visit `https://ahavah-preview.vercel.app/share/ABCDEFG` and confirm the UI renders.**

- [ ] **Step 3: Commit**

```bash
cd ahavah-web
git add src/app/share
git commit -m "app/share/[code]: inviter action surface for the referral email CTA

The referral-intro email's lime button points here. Web Share API
on mobile (native share sheet), copy-to-clipboard on desktop. The
displayed URL is /i/<code> — the invitee landing — so what the
inviter shares matches what the email body shows in plaintext."
```

---

### Task 12: Forward `inviter_code` from FE client functions

**Files:**
- Modify: `ahavah-web/src/lib/waitlist.ts`
- Modify: `ahavah-web/src/lib/beta.ts`
- Modify: `ahavah-web/src/lib/auth-otp.ts`

- [ ] **Step 1: Update `src/lib/waitlist.ts`**

Find the existing `postWaitlist` function. Replace:

```typescript
export async function postWaitlist(
  email: string,
  answers: WaitlistAnswers = {},
  antibot: AntibotPayload = {},
) {
  return apiClient.post<{ ok: boolean; isNew?: boolean }>("/waitlist", {
    email,
    answers,
    ...antibot,
  });
}
```

with:

```typescript
export async function postWaitlist(
  email: string,
  answers: WaitlistAnswers = {},
  antibot: AntibotPayload = {},
) {
  const { readReferralCode } = await import("@/lib/referrals");
  const inviter_code = readReferralCode();
  return apiClient.post<{ ok: boolean; isNew?: boolean }>("/waitlist", {
    email,
    answers,
    ...antibot,
    ...(inviter_code ? { inviter_code } : {}),
  });
}
```

- [ ] **Step 2: Update `src/lib/beta.ts`**

Replace the existing `registerBetaTester`:

```typescript
export async function registerBetaTester(
  email: string,
  antibot: AntibotPayload = {},
) {
  const { readReferralCode } = await import("@/lib/referrals");
  const inviter_code = readReferralCode();
  return apiClient.post<{ ok: boolean; isNew?: boolean }>("/beta-tester", {
    email,
    ...antibot,
    ...(inviter_code ? { inviter_code } : {}),
  });
}
```

- [ ] **Step 3: Update `src/lib/auth-otp.ts`**

Find the existing `requestEmailOtp`. Replace:

```typescript
export async function requestEmailOtp(
  email: string,
  antibot: AntibotPayload = {},
): Promise<void> {
  const res = await apiClient.post<RequestOtpResponse>("/request-otp", {
    email,
    ...antibot,
  });
  setSessionToken(res.session_token);
}
```

with:

```typescript
export async function requestEmailOtp(
  email: string,
  antibot: AntibotPayload = {},
): Promise<void> {
  const { readReferralCode } = await import("@/lib/referrals");
  const inviter_code = readReferralCode();
  const res = await apiClient.post<RequestOtpResponse>("/request-otp", {
    email,
    ...antibot,
    ...(inviter_code ? { inviter_code } : {}),
  });
  setSessionToken(res.session_token);
}
```

- [ ] **Step 4: Commit**

```bash
cd ahavah-web
git add src/lib/waitlist.ts src/lib/beta.ts src/lib/auth-otp.ts
git commit -m "lib: forward inviter_code from localStorage on public POSTs

postWaitlist, registerBetaTester, requestEmailOtp now merge
inviter_code into the request body when readReferralCode() returns
a well-formed Crockford code. Same conditional-spread pattern as
the existing antibot payload."
```

---

### Task 13: Deploy + verify + run blast

This is the operational task. No code changes; all manual.

- [ ] **Step 1: Push backend to ahavah/main**

```bash
cd ahavah-api
git push origin ahavah/main
```

- [ ] **Step 2: Watch the GHA run go green**

```bash
gh run watch $(gh run list --branch ahavah/main --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

Expected: `✓ Pull + rebuild api/chat in ~5m`. Migration 0024 applies automatically.

- [ ] **Step 3: Verify migration landed**

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c '\\d referral'"
```

Expected: table description with all columns from migration 0024. Also:

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   \"SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint \
     WHERE conrelid = 'token_ledger'::regclass AND conname = 'token_ledger_reason_check';\""
```

Expected: the CHECK clause includes `'referral'`.

- [ ] **Step 4: Deploy frontend preview**

```bash
cd ahavah-web
vercel deploy --token <VERCEL_TOKEN>
# capture the preview URL printed at the end
vercel alias set <preview-url> ahavah-preview.vercel.app --token <VERCEL_TOKEN>
```

- [ ] **Step 5: Smoke-test the /i/ landing**

Open `https://ahavah-preview.vercel.app/i/ABCDEFG` in a browser. Expected:
- 302 to `/waitlist` (since pre-launch flag is still on for the preview env)
- `document.cookie` shows `ahavah.ref=ABCDEFG`
- `localStorage.getItem('ahavah.ref')` returns `'ABCDEFG'` after the page settles

- [ ] **Step 6: Dry-run the email CLI in the container**

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-api-1 python -m emails.send_referral_intro"
```

Expected: a printed list of 15 beta_signup rows + their freshly-minted referral codes. No mail sent. If the list is empty, check `referral_intro_sent_at` isn't already populated (it shouldn't be on a fresh deploy).

- [ ] **Step 7: Send a test to e2e@techbaseltd.com (which is on the suppression list — will be skipped) AND to a real test inbox you control**

The suppression list filter:

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-api-1 python -m emails.send_referral_intro \
   --only e2e@techbaseltd.com"
```

Expected output: the CLI prints `Sending single test to e2e@techbaseltd.com (code=XXXXXXX) ...` BUT inside `send_referral_intro`, `is_suppressed_send` returns True so SMTP fires zero requests. Confirms the safety filter.

Now send to a real address you control (e.g. your personal Gmail):

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-api-1 python -m emails.send_referral_intro \
   --only YOUR_PERSONAL_GMAIL"
```

Open Gmail; visually confirm:
- Subject reads "Your link to bring someone in"
- Preheader reads "You earn a Boost for each friend who joins through your link."
- Title image "Bring someone with you." renders with lavender period (dark mode shows white text + lavender period)
- Lime CTA "Share your link →" is full-width
- Plaintext URL below is monospace, in indigo, wraps cleanly
- Footer Unsubscribe link is present

If anything looks broken, fix it (probably the title PNG render or a token in the template) and re-deploy. Do NOT proceed to the full blast until the test email looks right.

- [ ] **Step 8: Run the full blast**

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-api-1 python -m emails.send_referral_intro --all"
```

Expected output: `done — 13 sent, 2 skipped` (the 2 skipped are the `@techbaseltd.com` rows if they still exist; if you cleaned them up earlier, may be `15 sent, 0 skipped`).

- [ ] **Step 9: Verify codes are populated + emails marked sent**

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   \"SELECT email, referral_code, referral_intro_sent_at \
     FROM beta_signup WHERE unsubscribed_at IS NULL ORDER BY created_at\""
```

Expected: every row has both a code and a `referral_intro_sent_at` timestamp.

- [ ] **Step 10: Phase 1 done. Tag the milestone.**

```bash
cd ahavah-api
git tag phase1-referrals-blast-sent
git push origin phase1-referrals-blast-sent
```

---

## Phase 2 — Credit firing

Lands any time before launch but **must land before the first invitee actually graduates** (or those credits will silently stay in `graduated` state until a manual retro-sweep).

### Task 14: `_credit_one()` helper

**Files:**
- Modify: `ahavah-api/service/referrals/__init__.py`
- Modify: `ahavah-api/tests/test_referrals.py`

- [ ] **Step 1: Append the helper to `service/referrals/__init__.py`**

Add at the bottom (after `attribute()`):

```python
_Q_ALREADY_CREDITED = """
    SELECT 1 FROM token_ledger
     WHERE reason = 'referral'
       AND metadata->>'referral_id' = %(referral_id)s
     LIMIT 1
"""


def _credit_one(tx, person_uuid: str, referral_id: str) -> bool:
    """Idempotent +5 token credit. Returns True if a new ledger row was
    inserted, False if a row already exists for this referral_id (safe
    no-op, never should happen but defends against concurrent /finish-
    onboarding races on the same email).

    Caller is responsible for flipping the referral row to
    status='credited' AFTER this returns True."""
    if tx.execute(
        _Q_ALREADY_CREDITED, dict(referral_id=referral_id)
    ).fetchone() is not None:
        return False

    # Avoid a circular import — service.tokens depends on nothing in
    # service.referrals but the inverse needs late binding.
    from service.tokens import credit
    credit(
        tx,
        person_uuid,
        5,
        reason="referral",
        metadata={"referral_id": referral_id},
    )
    return True
```

- [ ] **Step 2: Append a unit test for the idempotency guard's pre-check string**

In `tests/test_referrals.py`, add at the bottom:

```python
def test_credit_one_pre_check_query_is_parameterized():
    """Defense-in-depth: the pre-check query must use a named parameter
    for referral_id, not string-format it (audit Data Integrity #9 lesson).
    Pure structural check — runs without a DB."""
    from service.referrals import _Q_ALREADY_CREDITED
    assert "%(referral_id)s" in _Q_ALREADY_CREDITED
    assert "format" not in _Q_ALREADY_CREDITED.lower()
    assert "f'" not in _Q_ALREADY_CREDITED
```

- [ ] **Step 3: Run tests**

```bash
cd ahavah-api && python -m pytest tests/test_referrals.py -v
```

Expected: all tests pass (5 from Task 2 + 1 new = 6).

- [ ] **Step 4: Commit**

```bash
cd ahavah-api
git add service/referrals/__init__.py tests/test_referrals.py
git commit -m "service/referrals: _credit_one helper (idempotent +5 credit)

Pre-check via metadata->>'referral_id' lookup so concurrent /finish-
onboarding races on the same email cannot double-credit. Mirrors the
audit-shipped pattern from service/checkout/_credit_subscription_stipend."
```

---

### Task 15: `credit_pending_for_invitee()`

**Files:**
- Modify: `ahavah-api/service/referrals/__init__.py`

- [ ] **Step 1: Append the function**

```python
_Q_FLIP_INVITEE_GRADUATED = """
    UPDATE referral
       SET status = 'graduated', graduated_at = NOW()
     WHERE invitee_email = %(invitee_email)s
       AND status = 'pending'
    RETURNING id, inviter_email
"""

_Q_INVITER_PERSON_UUID = """
    SELECT uuid::TEXT AS uuid FROM person
     WHERE normalized_email = %(email)s
        OR email = %(email)s
     LIMIT 1
"""

_Q_FLIP_GRADUATED_TO_CREDITED = """
    UPDATE referral
       SET status = 'credited', credited_at = NOW()
     WHERE id = %(id)s
       AND status = 'graduated'
    RETURNING id
"""


def credit_pending_for_invitee(
    tx, invitee_email: str, invitee_person_uuid: str
) -> Optional[str]:
    """Called at the invitee's POST /finish-onboarding inside the same
    api_tx that creates their person row. Returns the inviter's
    person.uuid iff a credit fired, else None."""
    norm = _normalize_email(invitee_email)
    row = tx.execute(
        _Q_FLIP_INVITEE_GRADUATED, dict(invitee_email=norm)
    ).fetchone()
    if row is None:
        return None  # nothing pending for this invitee

    referral_id = str(row["id"])
    inviter_email = row["inviter_email"]
    inviter_row = tx.execute(
        _Q_INVITER_PERSON_UUID, dict(email=inviter_email)
    ).fetchone()
    if inviter_row is None:
        return None  # inviter hasn't graduated yet; leave at 'graduated'

    inviter_uuid = inviter_row["uuid"]
    if _credit_one(tx, inviter_uuid, referral_id):
        tx.execute(_Q_FLIP_GRADUATED_TO_CREDITED, dict(id=referral_id))
    return inviter_uuid
```

- [ ] **Step 2: Commit**

```bash
cd ahavah-api
git add service/referrals/__init__.py
git commit -m "service/referrals: credit_pending_for_invitee (graduated → credited)

Flips referral row pending→graduated on the invitee's /finish-
onboarding. If the inviter already has a person row, credits +5 and
flips graduated→credited in the same tx. Else leaves at 'graduated'
for credit_pending_for_inviter to drain later."
```

---

### Task 16: `credit_pending_for_inviter()`

**Files:**
- Modify: `ahavah-api/service/referrals/__init__.py`

- [ ] **Step 1: Append the function**

```python
_Q_PENDING_FOR_INVITER = """
    SELECT id FROM referral
     WHERE inviter_email = %(inviter_email)s
       AND status = 'graduated'
"""


def credit_pending_for_inviter(
    tx, inviter_email: str, inviter_person_uuid: str
) -> int:
    """Called at the inviter's own POST /finish-onboarding (typically at
    launch sign-in). Drains all referrals where the invitees already
    graduated but the inviter wasn't yet a person. Returns the count
    actually credited."""
    norm = _normalize_email(inviter_email)
    rows = tx.execute(
        _Q_PENDING_FOR_INVITER, dict(inviter_email=norm)
    ).fetchall()
    n = 0
    for r in rows:
        referral_id = str(r["id"])
        if _credit_one(tx, inviter_person_uuid, referral_id):
            tx.execute(_Q_FLIP_GRADUATED_TO_CREDITED, dict(id=referral_id))
            n += 1
    return n
```

- [ ] **Step 2: Commit**

```bash
cd ahavah-api
git add service/referrals/__init__.py
git commit -m "service/referrals: credit_pending_for_inviter (drain escrow on inviter graduate)

Called at the inviter's own /finish-onboarding. For every referral
WHERE inviter_email = X AND status='graduated', credits +5 and flips
to 'credited'. Idempotent via _credit_one's pre-check."
```

---

### Task 17: Wire credit functions into `post_finish_onboarding`

**Files:**
- Modify: `ahavah-api/service/person/__init__.py` (`post_finish_onboarding`)

- [ ] **Step 1: Add the import at the top of `service/person/__init__.py`**

```python
from service.referrals import (
    attribute as attribute_referral,
    credit_pending_for_invitee,
    credit_pending_for_inviter,
)
```

(If `attribute_referral` was already imported in Task 5, keep that one and extend the import line.)

- [ ] **Step 2: Find `post_finish_onboarding`**

Run: `grep -n "def post_finish_onboarding" ahavah-api/service/person/__init__.py`

Today this is around line 648.

- [ ] **Step 3: Add credit calls after the `Q_FINISH_ONBOARDING` execute returns the new person row**

Find the existing block:

```python
        tx.execute(Q_FINISH_ONBOARDING, params=api_params)
```

Read the next ~30 lines to find where the new person's `uuid` and `email` are accessible. Typically the query returns the new person row directly. Add immediately after that point, still inside the `with api_tx() as tx:` block:

```python
        # Referral credits — see docs/superpowers/specs/2026-06-05-beta-referrals-design.md.
        # Both calls are idempotent; harmless when the user has no
        # referral relationships in either direction.
        new_email = row["email"]   # NOTE: adapt variable name to the actual row binding above
        new_uuid = str(row["uuid"])
        credit_pending_for_invitee(tx, new_email, new_uuid)
        credit_pending_for_inviter(tx, new_email, new_uuid)
```

**Important:** the implementer must verify the exact variable names used in the existing function. If `row` isn't the right binding, substitute (e.g. the spec scaffold uses `row["person_uuid"]` from `Q_FINISH_ONBOARDING`'s RETURNING).

- [ ] **Step 4: Smoke test plan (manual after Phase 2 deploy)**

End-to-end:

1. As beta_tester A: `POST /request-otp` → check OTP → `POST /check-otp` → walk wizard → `POST /finish-onboarding`. A is now in `person`.
2. Mint a code for A via `service.referrals.mint_code` (or via the email blast which already did this).
3. As fresh user B: open `/i/<A-code>`, walk through `/auth/sign-up` → check OTP → wizard → finish. Confirm `SELECT * FROM referral WHERE invitee_email = B` returns a row with `status='credited'` and `SELECT * FROM token_ledger WHERE reason='referral' AND metadata->>'referral_id' = <id>` returns one +5 row keyed to A's person.uuid.

- [ ] **Step 5: Commit**

```bash
cd ahavah-api
git add service/person/__init__.py
git commit -m "post_finish_onboarding: drive referral credits in both directions

After Q_FINISH_ONBOARDING inserts the new person row, call:
  - credit_pending_for_invitee — flips pending→graduated and credits
    inviter if they already graduated
  - credit_pending_for_inviter — drains any escrowed credits from
    invitees who already graduated before this user

Both are idempotent; harmless for users with no referral relationships."
```

---

### Task 18: `GET /referrals/me` (authed)

**Files:**
- Create: `ahavah-api/service/api/referrals_routes.py`
- Modify: `ahavah-api/service/api/__init__.py`
- Modify: `ahavah-api/service/referrals/__init__.py` (add `get_my_stats`)

- [ ] **Step 1: Append `get_my_stats` to `service/referrals/__init__.py`**

```python
_Q_MY_STATS = """
    WITH me AS (
        SELECT email, uuid FROM person WHERE uuid = %(uuid)s
    ),
    my_code AS (
        SELECT referral_code FROM beta_signup
         WHERE email = (SELECT email FROM me)
    ),
    my_refs AS (
        SELECT
            count(*)                                          AS joined_count,
            count(*) FILTER (WHERE status = 'credited')       AS credited_count
          FROM referral
         WHERE inviter_email = (SELECT email FROM me)
    ),
    my_pending_value AS (
        SELECT count(*) * 5 AS pending_token_balance
          FROM referral
         WHERE inviter_email = (SELECT email FROM me)
           AND status IN ('pending', 'graduated')
    )
    SELECT
        (SELECT referral_code FROM my_code)               AS code,
        (SELECT joined_count FROM my_refs)                AS joined_count,
        (SELECT credited_count FROM my_refs)              AS credited_count,
        (SELECT pending_token_balance FROM my_pending_value) AS pending_token_balance
"""


def get_my_stats(tx, person_uuid: str) -> dict:
    """For GET /referrals/me. Returns
        {code, joined_count, credited_count, pending_token_balance}
    code may be None if the caller isn't in beta_signup."""
    row = tx.execute(_Q_MY_STATS, dict(uuid=person_uuid)).fetchone()
    return {
        "code": (row or {}).get("code"),
        "joined_count": int((row or {}).get("joined_count") or 0),
        "credited_count": int((row or {}).get("credited_count") or 0),
        "pending_token_balance": int((row or {}).get("pending_token_balance") or 0),
    }
```

- [ ] **Step 2: Create the routes module**

Path: `ahavah-api/service/api/referrals_routes.py`

```python
"""Public + authed referral routes.

  GET /referrals/me — authed, returns the caller's code + counters.

Phase-2 surface. The public POST /referrals/code (for the pre-launch
<ReferralCard>) is intentionally NOT shipped here; Phase 1's email
blast covers the introduction. Add it later if a UI surface is built.
"""
from __future__ import annotations

from service.api.decorators import aget
from database import api_tx
from service.referrals import get_my_stats


@aget("/referrals/me")
def get_referrals_me(s):
    """Returns {code, joined_count, credited_count, pending_token_balance}."""
    with api_tx() as tx:
        return get_my_stats(tx, s.person_uuid)
```

- [ ] **Step 3: Mount the routes**

In `ahavah-api/service/api/__init__.py`, find the existing line near the bottom:

```python
import service.api.beta_routes  # noqa: E402,F401
```

Add directly after:

```python
import service.api.referrals_routes  # noqa: E402,F401
```

- [ ] **Step 4: Commit**

```bash
cd ahavah-api
git add service/referrals/__init__.py \
        service/api/referrals_routes.py \
        service/api/__init__.py
git commit -m "GET /referrals/me + service.referrals.get_my_stats

Authed endpoint returning {code, joined_count, credited_count,
pending_token_balance}. Single-CTE SQL keeps it cheap; runs on every
profile/referrals page view post-launch.

POST /referrals/code (for the pre-launch ReferralCard) deliberately
not shipped here — Phase 1's email blast is the introduction; the
in-app card lands only if a UI iteration calls for it."
```

---

### Task 19: Phase 2 deploy + smoke + tag

- [ ] **Step 1: Push**

```bash
cd ahavah-api
git push origin ahavah/main
```

- [ ] **Step 2: Watch GHA**

```bash
gh run watch $(gh run list --branch ahavah/main --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
```

- [ ] **Step 3: End-to-end credit-firing smoke test**

Use two test emails on the gmail.com allowlist (or temporarily widen the allowlist) and:

1. Sign up tester A through the standard flow. Note A's email.
2. SSH into the droplet, mint A's code:
   ```bash
   ssh ... 'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
     "SELECT referral_code FROM beta_signup WHERE email='\''A@email.com'\''"'
   ```
3. As fresh tester B, open `https://ahavah-preview.vercel.app/i/<A-code>` → cookies + localStorage set → walk through signup + onboarding → finish.
4. Verify:
   ```sql
   SELECT id, status, credited_at FROM referral WHERE invitee_email = 'B@email.com';
   -- expected: status='credited', credited_at not null
   SELECT delta, reason, metadata FROM token_ledger 
    WHERE reason='referral' AND metadata->>'referral_id' = '<id from above>';
   -- expected: one row, delta=5
   ```

- [ ] **Step 4: Tag**

```bash
cd ahavah-api
git tag phase2-referrals-credits-live
git push origin phase2-referrals-credits-live
```

---

## Self-review

After writing this plan, walked through the two specs and confirmed:

| Spec section | Plan task(s) |
|---|---|
| Migration 0024 (data model) | Task 1 |
| `service/referrals/` module — `mint_code`, `attribute` | Task 2 |
| `service/referrals/` module — `credit_pending_for_*`, `_credit_one`, `get_my_stats` | Tasks 14, 15, 16, 18 |
| Endpoint changes — `POST /beta-tester` | Task 4 |
| Endpoint changes — `POST /request-otp` | Task 5 |
| Endpoint changes — `POST /finish-onboarding` | Task 17 |
| Endpoint changes — `GET /referrals/me` | Task 18 |
| Endpoint changes — `POST /referrals/code` | **Deferred** (see Task 18 note) — Phase 1's email blast supersedes the pre-launch UI; can land in a future iteration if a `<ReferralCard>` is built |
| Frontend `/i/[code]` route + cookie | Task 10 |
| Frontend `/share/[code]` page | Task 11 |
| Storage key + `src/lib/referrals.ts` | Task 9 |
| Client-function propagation | Task 12 |
| `<ReferralCard>` component | **Deferred** — Phase 2 deliberate cut; v1 has no in-app pre-launch surface beyond the email |
| Email template + title PNGs | Tasks 6, 7 |
| Email CLI | Task 8 |
| Cross-cutting dependency review | Verified during plan writing; no surprises |
| End-to-end flow walkthroughs (A–D) | Implicitly covered by Tasks 13 (Flow A) and 19 (Flow B + C + D via the credit smoke test) |
| Error handling matrix | Each task's failure paths handled via explicit `return None` semantics and idempotent SQL; the matrix from the spec is preserved by the implementation |
| Test plan (10 unit + 1 integration + 5 manual smoke) | Tasks 2 + 14 (5 unit tests inline); manual smokes at Tasks 13, 17, 19 |
| Phase 2 deferral list | Honored — leaderboards / click tracking / tiered rewards / cron sweeper not in plan |

**Placeholder scan:** no TBDs, TODOs, "implement later", or hand-waves. Each step has either complete code, a verifiable command, or a clearly-bounded inspection action.

**Type / signature consistency:** all SQL parameter names match the Python dict keys passed to `tx.execute`. All function signatures and return types are consistent across tasks (e.g. `mint_code(tx, email)` returns `Optional[str]` in both Task 2 and Task 8's usage).

**Gaps found and noted:** Task 17 (the `post_finish_onboarding` wiring) requires the implementer to inspect the existing function to find the exact variable binding of the new person's uuid + email — this is called out explicitly in Step 3 with a "Important" note rather than left as a hand-wave, because the existing function's structure isn't in scope for this plan but the integration point is.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-05-beta-referrals-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Suits this plan well because each task is small and reviewable.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for review.

**Which approach?**
