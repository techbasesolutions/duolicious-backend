# Beta-tester referrals — Handover

> **Audience:** the next agent (fresh session, no prior context) picking up where this session left off.
> **Date:** 2026-06-06
> **Status:** Phase 1 SHIPPED to production. Phase 2 NOT STARTED.

## 0. TL;DR

- **Phase 1 + Phase 2 referrals are LIVE on prod.** Both tagged: `phase1-referrals-blast-sent`, `phase2-referrals-credits-live`. End-to-end verified with real DB writes on 2026-06-06.
- **Founding-member 6-month Premium grant is wired.** `service.entitlements.grant_founding_member_if_eligible` fires from `post_finish_onboarding` for anyone in beta_signup or with completed waitlist demographics. Promise from `waitlist_welcome.py` ("six months Premium free at launch") is now backed by code.
- **Referral-link click logging is live.** Migration 0025 + POST /referral-click + FE Route Handler awaits the click event. Query `referral_link_click` to see who's clicking which links, even if they never sign up.
- **Reengagement cron** now excludes beta testers with a `person` row (fix for harrigan-style false positives).
- **Signup allowlist** widened to all 7 cohort domains + icloud.
- Tag `june15-readiness-and-click-tracking` marks the cumulative milestone.

## What's left for June 15 launch

Nothing on the backend that I'm aware of. App-readiness (FE features, polish, R5 4-state coverage) is the remaining unknown — verify with PROJECT-STATUS.md.

The actual `beta_launch` email goes out via:
```bash
ssh ... "docker exec ahavah-api-api-1 python -m emails.send_beta_launch --all"
```
That email already exists (`emails/beta_launch.py`) and is dry-run-safe.

## 1. Where everything is

| Artifact | Path |
|---|---|
| Parent design spec | `ahavah-api/docs/superpowers/specs/2026-06-05-beta-referrals-design.md` |
| Email design spec (companion) | `ahavah-api/docs/superpowers/specs/2026-06-05-referral-intro-email-design.md` |
| Implementation plan (Phase 1 + Phase 2) | `ahavah-api/docs/superpowers/plans/2026-06-05-beta-referrals-implementation.md` |
| This handover | `ahavah-api/docs/superpowers/handovers/2026-06-06-beta-referrals-handover.md` |
| Phase 1 milestone tag | `ahavah-api: phase1-referrals-blast-sent` (pushed to origin) |

## 2. What landed in Phase 1

### Backend (`ahavah-api`, branch `ahavah/main`, all commits pushed to `github.com/techbasesolutions/duolicious-backend`)

Commits since the plan (`f924af0`), oldest first:

```
d4ea736  migration 0024: referrals schema + extend token_ledger enum
f3077b5  service/referrals: mint_code + attribute (Phase 1 surface)
df6eaa1  duotypes: add inviter_code field to PostBetaTester + PostRequestOtp
d3dcfb3  beta_routes: record referral attribution on new beta opt-ins
1774cd0  post_request_otp: record referral attribution on new sessions
65596c8  scripts: CDP title-image PNG renderer (reusable for email family)
39765af  emails: referral_intro template (one-shot blast to beta cohort)
1121100  emails: send_referral_intro CLI (one-shot blast)
ac6aea9  duotypes: fix Crockford regex (was including L)
28c7b60  referral_intro: drop em dashes; widen render viewport so title isn't clipped
00c36cb  emails: drop lime left stroke on callout; cache-bust title PNG
e1a724c  post_waitlist: record referral attribution on new waitlist signups
c655fe9  referral_intro: replace closing graf with founding-members framing
d31e44e  send_referral_intro: restrict blast to completed-onboarding cohort
9640630  service/referrals: SAVEPOINT mint_code retries + defensive inviter normalize
```

### Frontend (`ahavah-web`, branch `master`, deployed to ahavah.app via `vercel --prod`)

```
c1e3047  app/i/[code]: referral landing route + cookie-to-localStorage mirror   ← later REPLACED by ccbf13a
4f01dcf  lib: referral-code helpers (read/write/clear from localStorage)
57ddf56  email assets: title-referral{,-wht}.png for referral_intro template
07b6cf6  app/share/[code]: inviter action surface for the referral email CTA
ca99420  lib: forward inviter_code from localStorage on public POSTs
ccbf13a  app/i/[code]: replace Server Component with Route Handler   ← supersedes c1e3047
a098eae  ref-code regex: fix Crockford bug (was including L)
c137f8e  email assets: re-render title-referral PNGs (un-clipped)
```

The `c1e3047` server-component `/i/[code]/page.tsx` was DELETED and REPLACED by `ccbf13a` as `route.ts` (Next 16 rejected `cookies().set()` in Server Components). That history matters if you grep — only the route.ts file exists today.

## 3. Architecture (1-screen recap)

```
beta-tester gets email → email contains personal /i/<code> link
   ↓
invitee clicks link
   ↓
GET /i/<code>  (Vercel Route Handler — Next 16, src/app/i/[code]/route.ts)
   ├─ validate Crockford regex
   ├─ Set-Cookie: ahavah.ref=<code>; Max-Age=90d; SameSite=lax
   └─ 307 → /waitlist (pre-launch) OR /auth/sign-up (post-launch)
   ↓
destination page mounts, useEffect mirror copies cookie → localStorage
   ↓
user fills form, submits
   ↓
FE postWaitlist / registerBetaTester / requestEmailOtp
   ├─ readReferralCode() from localStorage
   └─ merge { inviter_code } into request body
   ↓
backend POST /waitlist | /beta-tester | /request-otp
   ├─ Pydantic validates inviter_code matches ^[0-9A-HJKM-NP-TV-Z]{7}$
   ├─ within same api_tx as the signup write:
   │    attribute(tx, req.inviter_code, req.email)
   │       ├─ no-op if code malformed / unknown / self-referral
   │       └─ INSERT INTO referral (inviter_email, invitee_email)
   │          ON CONFLICT (invitee_email) DO NOTHING
   │          status='pending', created_at=NOW()
   └─ commit
```

**Phase 2 will add** (after `Q_FINISH_ONBOARDING` inside `post_finish_onboarding`):
```
credit_pending_for_invitee(tx, person_uuid, email)
   ├─ flip the row's status pending → graduated
   ├─ check if inviter has ALSO graduated
   └─ if both graduated: _credit_one() each side (+5 tokens, status=credited)

credit_pending_for_inviter(tx, person_uuid, email)
   └─ symmetric: when the inviter finishes, find their graduated invitees,
      check both-sides condition, fire _credit_one() on the matched pairs.
```

## 4. Production state right now

| Thing | Count / value |
|---|---|
| beta_signup rows | 20 |
| beta_signup with referral_code | 20 |
| beta_signup with referral_intro_sent_at | 19 (18 from blast + 1 from test to harrigan.tennyson@gmail.com) |
| Cohort that completed onboarding (eligible for blast) | 18 |
| Cohort that did NOT complete onboarding | 2 (`harrigan.tennyson@gmail.com` — admin/test inbox; `jpjbraden@gmail.com` — never finished) |
| referral table rows (pending/graduated/credited) | 0 |
| token_ledger reason enum | now includes `'referral'` |

`jpjbraden@gmail.com` has a referral_code minted but `referral_intro_sent_at IS NULL`. If they complete waitlist onboarding later, the CLI will pick them up on the next `--all`. That's intentional — the blast filter is "completed onboarding AND not previously emailed", which is idempotent and self-healing.

## 5. How to monitor for inbound clicks + attributions

```bash
# Watch new referral rows in real-time (status 'pending' = signup just happened, no credits yet):
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   'SELECT inviter_email, invitee_email, status, created_at FROM referral ORDER BY created_at DESC LIMIT 20;'"

# Per-inviter funnel:
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   \"SELECT bs.email, bs.referral_code,
            COUNT(r.id) FILTER (WHERE r.status = 'pending') AS pending,
            COUNT(r.id) FILTER (WHERE r.status = 'graduated') AS graduated,
            COUNT(r.id) FILTER (WHERE r.status = 'credited') AS credited
       FROM beta_signup bs
       LEFT JOIN referral r ON r.inviter_email = bs.email
      WHERE bs.referral_intro_sent_at IS NOT NULL
      GROUP BY bs.email, bs.referral_code
      ORDER BY pending DESC, graduated DESC;\""
```

## 6. Phase 2 — what to build

The plan's Tasks 14–19 cover this exactly. Brief summary:

### Task 14: `_credit_one()` + `credit_pending_for_{invitee,inviter}` helpers
- File: `service/referrals/__init__.py` — append (don't replace) the existing module.
- Adds `_Q_ALREADY_CREDITED`, `_Q_UPDATE_REFERRAL_GRADUATED`, `_Q_UPDATE_REFERRAL_CREDITED` (exact SQL in plan §Task 14).
- Token amount: `+5` per side. Reason: `'referral'`. Metadata: `{"referral_id": "<uuid>"}` for idempotency.
- The both-sides-graduated invariant is enforced inside `_credit_one()`: it'll return `False` (no insert) if the OTHER side hasn't graduated yet.

### Task 15: Extend `tests/test_referrals.py`
- Add unit tests for the new pure-logic pieces (the SQL constants don't need direct testing; integration is covered by the smoke).

### Task 16: Wire into `post_finish_onboarding`
- File: `service/person/__init__.py` (the `post_finish_onboarding` function — same file you already edited for `post_request_otp`).
- Insert AFTER `Q_FINISH_ONBOARDING` returns the row, BEFORE the commit:
  ```python
  credit_pending_for_invitee(tx, person_uuid, req.email)
  credit_pending_for_inviter(tx, person_uuid, req.email)
  ```
- Both functions are no-ops if the email has no referral row, so order doesn't matter.

### Task 17: `GET /referrals/me` endpoint
- New file: `service/api/referrals_routes.py`.
- Authed (uses existing session token / person decorator).
- Returns `{ code, share_url, pending, graduated, credited, total_credited_tokens }`.

### Task 18: Optional FE `<ReferralCard>` on /waitlist completion screen
- File: `ahavah-web/src/components/app/referral-card.tsx`.
- Mirrors `<BetaTesterCard>` pattern. Fetches `/referrals/me`. Shows the inviter's code + share button + funnel.

### Task 19: Manual deploy + smoke
- Push backend → GHA → migration is a no-op (0024 already applied).
- Deploy FE prod via `vercel --prod`.
- Smoke: have a test invitee complete onboarding, verify both sides get `+5` rows in `token_ledger` with reason `'referral'`.

## 7. Critical gotchas (read these BEFORE Phase 2 work)

### 7.1 Next 16 cookies in Server Components

Next 16 disallows `cookies().set()` during Server Component rendering. We caught this in Phase 1 smoke (digest `4180962077`) and converted `/i/[code]` from `page.tsx` to `route.ts`. If you build any new route that sets/modifies cookies, **use a Route Handler or Server Action, NOT a Server Component**.

### 7.2 Crockford regex

The regex `[0-9A-HJ-NP-TV-Z]` looks correct at a glance but the range `J-N` is `JKLMN` — five chars including L, which Crockford excludes. The correct regex is `[0-9A-HJKM-NP-TV-Z]` (32 chars total, excludes I/L/O/U). All Phase 1 sites were fixed in commit `ac6aea9` (BE) + `a098eae` (FE). If you add a new code-validating regex anywhere, use the corrected form.

### 7.3 `mint_code` retry needs SAVEPOINT

The retry loop in `mint_code()` wraps each iteration in a SAVEPOINT (`9640630`). If you refactor it, **keep the SAVEPOINT pattern** — without it, a unique-violation poisons the outer tx and the next `tx.execute()` raises `InFailedSqlTransaction`. Operationally unreachable at 34B keyspace, but defensive.

### 7.4 The `referral` table FK only cascades on inviter

```sql
inviter_email TEXT NOT NULL REFERENCES beta_signup(email) ON DELETE CASCADE,
invitee_email TEXT NOT NULL,  -- no FK
```

Deleting a beta_signup row CASCADE-deletes the referral rows where they're the INVITER, not where they're the INVITEE. If you ever need to wipe an invitee's data, explicitly `DELETE FROM referral WHERE invitee_email = ...` first.

### 7.5 EMAIL_ASSET_ORIGIN cache busting

Gmail's image proxy caches PNGs aggressively. If you re-render a title PNG (e.g. for a Phase 2 email), bump the URL query param: `title-foo.png?v=2`. We caught this in Phase 1 smoke when the un-clipped re-rendered PNG was still showing the old clipped version in Gmail.

### 7.6 NEVER recreate the api container without the production compose flags

`docker compose up -d --force-recreate api` (or `restart api`) uses ONLY the base `docker-compose.yml`, which has `DUO_DB_PASS: password` hardcoded. The real password lives in `docker-compose.production.yml` via `${POSTGRES_PASSWORD}` substitution from `.env.production`. Without the override, the api spawns with literal-string password "password" → fails on initapi.py's `psycopg.connect` → exits → 502 at the proxy.

ALWAYS use:
```bash
cd /opt/ahavah-api
docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production up -d <service>
```

This bit me on 2026-06-06 while widening the signup allowlist. Took ~5 min of outage to diagnose. Same trap applies to chat + cron services (all of them have prod-only env values in the override). The GHA deploy uses the right flags automatically; the trap is when you SSH in and run compose by hand.

Bonus: there's a latent bug in `database/initapi.py:34-37` — the `except psycopg.errors.OperationalError:` clause prints `e` but `e` isn't bound (no `as e`), so on real auth failure it raises a NameError instead of retrying. Worth fixing when you're in that area.

### 7.7 The CLI's pre-flight backfill is REQUIRED

`emails/send_referral_intro.py::_backfill_and_target_codes()` runs `mint_code` on every targeted row BEFORE returning the list. This ensures no email is sent without a code. If you write a new email-blast CLI, replicate that pattern: never separate "compute recipient list" from "ensure each recipient has the data the template needs."

### 7.7 The blast filter requires waitlist_signup.answers

The current `_Q_TARGETS` (commit `d31e44e`) JOINs `waitlist_signup` and requires `answers <> '{}'::jsonb`. Two beta_signup rows that never completed waitlist onboarding (`harrigan.tennyson@gmail.com`, `jpjbraden@gmail.com`) are excluded. If Phase 2 adds a re-blast or a different email to the same cohort, decide consciously whether to keep this filter.

## 8. Operational reference

### Backend deploy
```bash
cd /d/Antigravity/ahavah-api
git push origin ahavah/main
# GHA "Deploy ahavah/main → droplet" runs ~5-7 min:
# - applies migrations/*.sql via psql -f (idempotent — safe to re-run)
# - rebuilds api/chat/cron containers via docker-compose
gh run watch --repo techbasesolutions/duolicious-backend \
  $(gh run list --repo techbasesolutions/duolicious-backend --branch ahavah/main --limit 1 --json databaseId --jq '.[0].databaseId') \
  --exit-status
```

### Frontend deploy
```bash
cd /d/Antigravity/ahavah-web
# Preview:
vercel deploy --token <see [[vercel-token]] in agent memory>
vercel alias set <preview-url> ahavah-preview.vercel.app --token <token>
# Production:
vercel deploy --prod --token <token>
```

The Vercel token is in agent memory at `[[vercel-token]]`. Scope `techbase-hq`.

### SSH to droplet
```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27
# Containers: ahavah-api-api-1, ahavah-api-chat-1, ahavah-api-postgres-1, ahavah-api-cron-1, ahavah-api-status-1
# Compose dir: /opt/ahavah-api
# Env file:    /opt/ahavah-api/.env.production (backups: .env.production.bak.*)
```

### Running the CLI in prod
```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-api-1 python -m emails.send_referral_intro"          # dry run
ssh ... "docker exec ahavah-api-api-1 python -m emails.send_referral_intro --only EMAIL"
ssh ... "docker exec ahavah-api-api-1 python -m emails.send_referral_intro --all"
```

### Resetting a row's sent flag (for re-testing the email yourself)
```bash
ssh ... "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
  \"UPDATE beta_signup SET referral_intro_sent_at = NULL WHERE email = '...';\""
```

## 9. Agent memory references

These memories are still load-bearing — read before Phase 2:

- `[[ahavah-pwa]]` — overall project status pointer (READ FIRST)
- `[[ahavah-credentials]]` — droplet, DB, third-party keys
- `[[ahavah_ci_cd]]` — deploy automation
- `[[feedback_no_em_dashes]]` — no em dashes in user-facing strings (the email body fix)
- `[[feedback_no_partial_border_stroke]]` — no left-border-only callouts (the email callout fix)
- `[[feedback_renders_not_works]]` — visual verification means actually opening the rendered output, not class-name checks
- `[[vercel-token]]` — Vercel CLI token (techbase-hq scope)
- `[[reference_ahavah_windows_test_harness]]` — running Python tests on Windows (docker workaround; no local python)

## 10. Open follow-ups (not blockers)

| # | Item | Where | Severity |
|---|---|---|---|
| 1 | None right now — both Phase 1 quality flags resolved in commit `9640630` | — | — |

(Previous follow-ups from Phase 1 review: `mint_code` SAVEPOINT, defensive inviter normalization — both LANDED.)

## 11. How to resume

If you're a fresh agent picking this up:

1. Read this file end-to-end.
2. Read the parent spec `2026-06-05-beta-referrals-design.md` §"Phase 2" section.
3. Read the plan `2026-06-05-beta-referrals-implementation.md` Tasks 14–19.
4. Verify nothing has drifted: `gh run list --repo techbasesolutions/duolicious-backend --branch ahavah/main --limit 3` should show the most recent run as `success`.
5. Confirm prod state matches §4 of this doc: run the funnel SQL from §5.
6. Confirm the live `/i/<code>` route still works: `curl -I https://ahavah.app/i/G0BRYWE` → expect 307 + Set-Cookie.
7. If everything checks out, you can proceed to Phase 2 Task 14 directly without re-validating anything from Phase 1.

If state has drifted (e.g. someone unsubscribed, the cohort grew, a new beta tester was added), that's normal — the system is self-healing. Just re-run the funnel SQL to understand the current state before making decisions.
