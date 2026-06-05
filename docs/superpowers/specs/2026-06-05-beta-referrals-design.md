# Beta-tester referrals — design spec

**Date:** 2026-06-05
**Status:** Design, awaiting user approval before implementation plan
**Author:** Claude (brainstormed with admin@techbaseltd.com)
**Implementer:** TBD (next session via `writing-plans` skill)

## Problem

Ahavah has an active pre-launch beta cohort (15 opt-ins as of 2026-06-05) and 22 waitlist signups, growing organically through the existing `referral_source` field that tracks **how** users heard about Ahavah but not **who** referred them. One waitlist user (`ryrychristine@gmail.com`) explicitly cited "assembly" as her source today — the first congregation-driven referral — which validates that the loop already exists informally.

We want a **formal referral mechanism**:
- Each beta tester can share a unique link.
- When someone signs up through that link and graduates to a real account at launch, the inviter gets rewarded inside the existing token economy.
- The reward is held in escrow until both inviter and invitee have `person` rows; it cannot fire pre-launch because the inviter has no `person` row to credit.

## Decisions (from brainstorming, locked)

| Decision | Choice |
|---|---|
| Who can refer | **Beta opt-ins only** (rows in `beta_signup`) |
| Reward type | **Tokens**, credited via the existing `token_ledger` |
| Trigger | **Strictest:** reward fires only when both inviter and invitee have a `person` row created via `POST /finish-onboarding` |
| Quantity | **5 tokens** per successful referral (equivalent to one Boost) |
| Link shape | **One reusable code per inviter** (`ahavah.app/i/<code>`) |
| Initial distribution | One-shot **CLI script** generates codes for the current cohort and emails each tester their personal link |

## Out of scope for v1

These are intentional YAGNI cuts; surface them later if data justifies:

- Click tracking (counting `/i/<code>` hits that never convert)
- Tiered rewards (first-N referrals worth more)
- Single-use named codes ("Alice's invite to Bob")
- Live "X friends invited so far" counter visible to pre-launch inviters
- Leaderboards (deliberately anti-pattern for a relationship app)
- Authenticated `/profile/referrals` dashboard page — deferred to Phase 2; v1 ships only the email blast + the on-waitlist share card

## Architecture

### Data model — migration `0024_referrals.sql`

```sql
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

-- Extend token_ledger.reason enum to allow 'referral'. Constraint name
-- verified live: `token_ledger_reason_check`.
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

**Referral status state machine:**

```
  Self-referral (invitee email == inviter email) → no row created, attribute() returns None
  Bad / unknown code                              → no row created, attribute() returns None
  Successful attribution                          → row INSERTed with status='pending'

      attribute() ─────────────→ pending
                                    │
                                    │ invitee POST /finish-onboarding
                                    ▼
                                  graduated
                                    │
                                    │ at the moment the row enters 'graduated' AND
                                    │ the inviter already has a person row, OR later
                                    │ when the inviter's own POST /finish-onboarding fires
                                    ▼
                                  credited  (+5 tokens in token_ledger)
```

Idempotency: the `credited` state is terminal; both credit code paths exclude rows with `status='credited'`. Concurrent `/finish-onboarding` calls (impossible for the same person, but for two different invitees of the same inviter) commit in tx order; each row's transition `graduated → credited` is gated by `WHERE status = 'graduated'` so neither overwrites the other.

Defense-in-depth: the SQL-level `CHECK (inviter_email <> invitee_email)` is unreachable today because `attribute()` rejects self-referrals before INSERT, but the constraint stays as a guard against future code paths.

### Module — `service/referrals/`

```
service/referrals/
├── __init__.py
└── sql.py
```

Public surface (caller owns the `api_tx`):

```python
class ReferralCodeCollision(Exception):
    """Raised after 3 unique-violation retries minting a code. Operational
    signal that the alphabet/length needs bumping; never seen in practice
    at 32^7 = 34B keyspace."""


def mint_code(tx, email: str) -> str:
    """Idempotent. If beta_signup.referral_code is already set, return it.
    Else generate a unique Crockford-base32 code (alphabet:
    0123456789ABCDEFGHJKMNPQRSTVWXYZ, length 7), INSERT-or-SELECT pattern
    with a 3-try collision loop, return the code."""


def attribute(tx, inviter_code: Optional[str], invitee_email: str) -> Optional[ReferralRow]:
    """Called at the invitee's beta-join (and post-launch, request-otp).
    - Returns None if inviter_code is None / not found / invitee == inviter.
    - Else INSERTs a referral row ON CONFLICT (invitee_email) DO NOTHING.
    - Returns the row created, or None if invitee was already attributed."""


def credit_pending_for_invitee(tx, invitee_email: str, invitee_person_uuid: str) -> Optional[str]:
    """Called at invitee's /finish-onboarding.
    1. UPDATE referral SET status='graduated', graduated_at=NOW()
       WHERE invitee_email = $1 AND status = 'pending'
       RETURNING id, inviter_email
    2. If a row was returned, SELECT uuid FROM person WHERE email = inviter_email.
       - No row → inviter hasn't graduated yet; leave at 'graduated', return None.
       - Row found → call _credit_one(tx, inviter.uuid, referral.id), then
         UPDATE referral SET status='credited', credited_at=NOW()
         WHERE id = referral.id AND status='graduated'.
    Returns the inviter's person_uuid iff credit fired, else None."""


def credit_pending_for_inviter(tx, inviter_email: str, inviter_person_uuid: str) -> int:
    """Called at the inviter's /finish-onboarding (typically at launch
    sign-in). For every referral WHERE inviter_email = $1 AND status =
    'graduated', call _credit_one(tx, inviter_person_uuid, referral.id)
    and flip to status='credited'. Returns count credited."""


def get_my_stats(tx, person_uuid: str) -> MyReferralStats:
    """For the authed GET /referrals/me endpoint.
    Joins token_ledger + referral via inviter's email lookup. Returns
    {code, joined_count, credited_count, pending_token_balance}."""


# --- Internal helpers below this line ---

def _credit_one(tx, person_uuid: str, referral_id: str) -> bool:
    """Idempotent +5 to token_ledger. Pre-check:
    SELECT 1 FROM token_ledger WHERE reason='referral'
                              AND metadata->>'referral_id' = $1.
    If found, return False (already credited — shouldn't happen but safe).
    Else call service.tokens.credit(tx, person_uuid, 5, reason='referral',
    metadata={'referral_id': referral_id}) and return True."""
```

### Endpoint changes

| Endpoint | File | Change |
|---|---|---|
| `POST /beta-tester` | `service/api/beta_routes.py` | `PostBetaTester` gains `inviter_code: Optional[str] = Field(default=None, max_length=16, pattern=r'^[0-9A-HJ-NP-Z]+$')`. Handler: after `register_beta()` returns `is_new=True`, call `referrals.attribute(tx, req.inviter_code, req.email)` in the same tx. |
| `POST /request-otp` | `service/person/__init__.py` (`post_request_otp`) | Same `inviter_code` field on `PostRequestOtp`. Call `referrals.attribute(tx, req.inviter_code, req.email)` only when a brand-new `duo_session` row is created (returning sign-in doesn't attribute). |
| `POST /finish-onboarding` | `service/person/__init__.py` (`post_finish_onboarding`) | After `Q_FINISH_ONBOARDING`, call both `credit_pending_for_invitee(tx, person.email, person.uuid)` AND `credit_pending_for_inviter(tx, person.email, person.uuid)` in the same tx. |
| `POST /referrals/code` (**new, public**) | `service/api/referrals_routes.py` | Body `{email}`. Returns `{code}` if `email` is a row in `beta_signup`, else 404. Rate-limited via `shared_recipient_limit` to prevent enumeration. Used by the pre-launch `<ReferralCard>` on `/waitlist`. |
| `GET /referrals/me` (**new, authed**) | `service/api/referrals_routes.py` | Returns `MyReferralStats`. For post-launch UI. v1 implements the endpoint; the consuming page is deferred. |

### Frontend pieces

| File | Change |
|---|---|
| `src/lib/storage-keys.ts` | Add `export const REFERRAL_CODE_KEY = "ahavah.ref";` |
| `src/lib/referrals.ts` (**new**) | `readReferralCode()` (localStorage + cookie fallback), `clearReferralCode()`, `getReferralCode(email)` (public), `getMyReferralStats()` (authed). |
| `src/app/i/[code]/page.tsx` (**new**) | Server component. Validates code against Crockford-base32 regex; sets `ahavah.ref` cookie (`maxAge: 90 * 24 * 3600`, `sameSite: 'lax'`, `path: '/'`); redirects to `/waitlist` pre-launch (`NEXT_PUBLIC_PRELAUNCH !== 'false'`) or `/auth/sign-up` post-launch. |
| `src/app/waitlist/page.tsx`, `src/app/auth/sign-up/page.tsx` | Mount-time `useEffect` that mirrors `cookies['ahavah.ref'] → localStorage` once, so subsequent reads (across page nav) are localStorage-only. |
| `src/lib/waitlist.ts`, `src/lib/beta.ts`, `src/lib/auth-otp.ts` | Each `postWaitlist`, `registerBetaTester`, `requestEmailOtp` reads `readReferralCode()` and merges `inviter_code` into the POST body. Same pattern already used for `antibot` payloads. |
| `src/components/app/referral-card.tsx` (**new**) | Sibling of `<BetaTesterCard>`. Props: `email`. Fetches `POST /referrals/code` once, renders the inviter's link + copy/native-share affordances. Brand: lavender accent (mirrors `BetaTesterCard`). |
| `src/app/waitlist/page.tsx` (completion screen) | Render `<ReferralCard email={email} />` next to `<BetaTesterCard email={email} />` when beta opt-in is complete. |

### Email + CLI

| File | Change |
|---|---|
| `emails/referral_intro.py` (**new**) | New template using `emails.base` shell (logo, indigo chip, lime CTA). Subject: `"Your Ahavah invite link is ready"`. Body: brief framing, the personal link, "earn 5 tokens per friend who joins at launch", lime "Copy your link" CTA. Standard footer with unsubscribe via existing `service/unsubscribe.make_url(scope='beta', email=..., web_base=...)`. |
| Title PNGs | New `public/email/title-referral.png` + `title-referral-wht.png` rendered via the existing CDP script (`render-title-png.mjs` per prior session notes). |
| `emails/send_referral_intro.py` (**new**) | CLI mirroring `send_beta_launch.py`. `--all` flow: (1) backfill missing codes via `referrals.mint_code()`; (2) skip rows where `unsubscribed_at IS NOT NULL` OR `referral_intro_sent_at IS NOT NULL` OR `is_suppressed_send(email)`; (3) send + `UPDATE beta_signup SET referral_intro_sent_at = NOW() WHERE email = $1` per row. Re-running is idempotent. |

## Cross-cutting dependency review

| Dependency | Verified | Risk |
|---|---|---|
| `beta_signup.email TEXT PRIMARY KEY` works as FK target | ✅ migration 0020 | Low |
| `token_ledger.person_id UUID REFERENCES person(uuid)` — we credit by `person.uuid`, not `id` | ✅ migration 0014 | Low |
| `token_ledger_reason_check` is the actual CHECK constraint name | ✅ live query confirmed | Low |
| `service.tokens.credit()` is sync, tx-owning, signature `(tx, person_uuid, delta, reason, metadata)` | ✅ `service/tokens/__init__.py` | Low |
| `post_finish_onboarding` is the single hook for both credit triggers | ✅ `service/person/__init__.py:648` | Low |
| Pre-launch proxy redirects `/auth/sign-up` to `/waitlist` — `/i/[code]` handles via `NEXT_PUBLIC_PRELAUNCH` check | ✅ `src/proxy.ts` | Low |
| `is_suppressed_send` filters team / sample domains for email blast | ✅ `emails/base.py` | Low |
| `unsubscribed_at` already on `beta_signup` | ✅ migration 0023 | Low |
| Migration ordering: 0024 sorts after 0014/0020/0023 in GHA's `ls migrations/*.sql \| sort` | ✅ | Low |
| Adding `'referral'` to the CHECK constraint passes for all existing rows (no row uses that value yet) | ✅ trivially | Low |
| Pydantic-validated additive field on existing models matches prior precedent (honeypot, turnstile_token) | ✅ | Low |
| Same-origin `/referrals/*` calls fit existing CSP `connect-src 'self'` | ✅ `next.config.ts` | Low |
| Cookie size `ahavah.ref` ~8 bytes well under limits | ✅ | Low |
| Existing per-recipient rate limit on `/beta-tester` (5/hr + 20/day) blunts code-spamming | ✅ shipped P2 | Low |

### Risks called out but accepted

1. **`UNIQUE (invitee_email)` locks in the first attributing inviter.** If a user clicks Alice's link, refreshes mid-flow, then clicks Bob's link, the localStorage last-write-wins on the client but the `referral` row's INSERT-ON-CONFLICT-DO-NOTHING means the *first* attribution at signup time persists. Accepted as v1 behavior; documented so future devs don't add UPDATE logic thinking it's a bug.

2. **Orphaned `graduated` rows if the inviter never graduates.** Cheap to leave in the DB indefinitely; queryable for ops visibility. No cron sweeper needed.

3. **Same-IP self-referral via two distinct emails** (a user makes a sock-puppet on a different gmail and uses their own code). The trigger gate (`status='credited'` requires BOTH inviter and invitee to complete full graduation including photos + identity verification) makes this expensive but not impossible. Identity-verification scaling (Stripe Identity, currently 0 jobs run) is the structural defense here; the referral system itself does not attempt IP-based detection in v1.

## Data flow walkthroughs

### Flow A — pre-launch, both users are waitlist members

```
T+0   Inviter (beta tester) opens "Your Ahavah invite link is ready" email,
      copies their link ahavah.app/i/H7XK2QM, shares it.
T+1   Invitee clicks the link.
        → GET /i/H7XK2QM
        → cookies['ahavah.ref'] = 'H7XK2QM'
        → 302 → /waitlist
T+2   Invitee lands on /waitlist. useEffect mirrors cookie to localStorage.
T+3   Invitee enters email, submits.
        → POST /waitlist {email, answers, inviter_code: 'H7XK2QM'}
        → waitlist_signup row created
T+4   Invitee completes the demographic wizard.
        → POST /waitlist {email, answers: {...full}, inviter_code: 'H7XK2QM'}
        → waitlist_signup row updated
T+5   Invitee opts into beta on the completion screen.
        → POST /beta-tester {email, inviter_code: 'H7XK2QM'}
        → beta_signup row created
        → referrals.attribute(tx, 'H7XK2QM', invitee_email)
        → referral row INSERT, status='pending'
        → frontend clearReferralCode() so the next page doesn't re-attribute
T+...  (LAUNCH)
T+N   Invitee receives beta-launch email, signs in via OTP, finishes onboarding.
        → POST /finish-onboarding
        → person row created
        → credit_pending_for_invitee(tx, invitee_email, person.uuid)
            → referral row flips pending → graduated
            → inviter has no person row yet → no credit fires
        → credit_pending_for_inviter(tx, invitee_email, person.uuid)
            → no referrals with inviter_email = invitee_email → 0 credited
T+N+k Inviter (Alice) signs in for the first time post-launch.
        → POST /finish-onboarding
        → person row created
        → credit_pending_for_invitee(...) returns None (no row where Alice is invitee)
        → credit_pending_for_inviter(tx, alice_email, alice.uuid)
            → finds referral row where inviter_email = alice_email, status = 'graduated'
            → _credit_one(tx, alice.uuid, referral.id)
            → token_ledger row INSERT delta=+5 reason='referral'
            → referral row flips graduated → credited
            → returns count = 1
```

### Flow B — post-launch, both users join via /auth/sign-up

Same as Flow A but `/i/[code]` redirects to `/auth/sign-up` instead of `/waitlist`. Attribution fires at `POST /request-otp` (brand-new session) instead of `POST /beta-tester` (which is still legal post-launch but no longer required).

### Flow C — self-referral attempt

```
Inviter has code H7XK2QM. Submits beta opt-in for their OWN email
with their own code in the body.
  → referrals.attribute(tx, 'H7XK2QM', inviter_email)
  → Look up inviter_email from code → matches request email
  → return None (silently void)
  → beta-tester flow continues normally without a referral row
```

### Flow D — invitee gets second invite mid-flow

```
Invitee clicks Alice's link → cookie set to Alice's code.
Invitee navigates away. Comes back later, clicks Bob's link →
cookie overwritten to Bob's code.
Invitee submits /beta-tester {inviter_code: Bob's code}.
  → referrals.attribute(tx, Bob's code, invitee_email)
  → INSERT INTO referral ...; ON CONFLICT (invitee_email) DO NOTHING
  → If invitee was already attributed (Alice's link landed first via
    an earlier waitlist submit), no row is created and Bob gets nothing.
  → If invitee was NOT yet attributed, Bob's row is created.
```

## Error handling

| Failure mode | Behavior |
|---|---|
| Code minting hits 3 collisions in a row | Raise `ReferralCodeCollision`. Operationally rare at 34B keyspace; logs as ERROR with the email for manual intervention. Email blast skips this row and continues. |
| `attribute()` receives malformed `inviter_code` (e.g., 100-char garbage) | Pydantic `pattern` + `max_length=16` rejects at request validation → 400. |
| `attribute()` receives a well-formed but unknown code | Treated as "no inviter". Beta-join still succeeds; no referral row created. |
| `_credit_one()` fails the pre-check (already credited) | Returns False; outer caller logs but does not abort the tx. |
| `_credit_one()` raises an unexpected exception | Bubbles up; `post_finish_onboarding` will roll back the whole tx including person creation. This is the correct safety posture — onboarding fails loud rather than silently losing a credit. |
| Email blast SMTP failure mid-run | Per-row `referral_intro_sent_at` write is atomic with the send; re-running the CLI picks up where it left off. |
| `referral_intro_sent_at` written but Resend bounced silently | Same as any other email send in this codebase: visible only via Resend dashboard. Resend webhook integration is out of scope for v1. |

## Testing

### Unit tests (`tests/test_referrals.py`)

| Test | What it covers |
|---|---|
| `test_mint_code_idempotent` | Calling `mint_code` twice on the same email returns the same code. |
| `test_mint_code_unique_across_emails` | Two emails get distinct codes. |
| `test_mint_code_format` | Code is 7 chars from the Crockford-base32 alphabet. |
| `test_attribute_self_referral_voided` | `attribute(inviter_code, inviter_email)` returns None and creates no row. |
| `test_attribute_unknown_code_returns_none` | Bad code → None, no row. |
| `test_attribute_duplicate_invitee_first_wins` | Two `attribute` calls for same invitee, different inviters → only the first row persists. |
| `test_credit_invitee_before_inviter` | Pre-launch flow: invitee graduates first, row sits at `graduated`. Then inviter graduates → row flips to `credited`, ledger +5. |
| `test_credit_invitee_after_inviter` | Post-launch flow: inviter has person row already. Invitee graduates → row flips straight to `credited`, ledger +5. |
| `test_credit_idempotency` | Calling both credit functions twice on the same row never double-credits (ledger has exactly one +5 entry). |
| `test_credit_multiple_invitees_for_one_inviter` | Inviter graduates with 3 graduated referrals → ledger gets 3 separate +5 entries. |

### Integration test (`test/functionality4/referrals.sh` or similar shell harness already used in the repo)

End-to-end: spin up the test stack, register 2 beta testers via API, mint a code for one, simulate the other clicking the link and joining via the second `/beta-tester` call, simulate both graduating, assert the inviter's `token_ledger` SUM(delta) where reason='referral' equals 5.

### Manual smoke tests (post-deploy)

1. `python -m emails.send_referral_intro` (dry run) → lists current 15 beta testers, prints each generated code.
2. `python -m emails.send_referral_intro --only admin@techbaseltd.com` → ignored by suppression filter (proves the safety net).
3. `python -m emails.send_referral_intro --only e2e@techbaseltd.com` → fires a real send to the test address (we control this inbox).
4. `curl /i/ABCD123 -i` from the live preview → 302 to `/waitlist` (or `/auth/sign-up` if prelaunch flag flipped).
5. Open `/i/H7XK2QM` in a browser, complete a fresh signup, query DB: `SELECT * FROM referral ORDER BY created_at DESC LIMIT 1` shows the row.

## Migration & deploy plan

1. Land migration `0024_referrals.sql` (it will run automatically on the next ahavah/main deploy via the existing GHA migration loop in `deploy-ahavah.yml`).
2. Land backend module + route + endpoint extensions in one commit.
3. Land frontend changes (storage key, lib, `/i/[code]` route, share card) in one commit.
4. Run `python -m emails.send_referral_intro` dry-run on the droplet to verify the code-backfill + recipient list.
5. Run `python -m emails.send_referral_intro --only e2e@techbaseltd.com` to verify deliverability.
6. Run `python -m emails.send_referral_intro --all` to blast the cohort.
7. Watch `beta_signup` table for new attributed signups via `SELECT * FROM referral`.

## Phase 2 (after launch, not in this spec's scope)

- Authed `/profile/referrals` page consuming `GET /referrals/me`.
- Live counter on the waitlist completion screen (requires email-based auth, currently blocked by enumeration risk).
- Tiered rewards if data justifies (first 3 referrals = 10 each, etc.).
- Cron sweeper that emails inviters quarterly with a status update on their referrals.
- Click tracking (`referral_clicks` table).
- Adapter for non-beta_signup inviters once the beta phase ends (the schema generalises by replacing `inviter_email REFERENCES beta_signup(email)` with `inviter_email REFERENCES person(email)` — straightforward migration).
