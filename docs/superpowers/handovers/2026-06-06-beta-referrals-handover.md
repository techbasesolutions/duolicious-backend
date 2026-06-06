# Beta-tester referrals — Handover

> **Audience:** the next agent (fresh session, no prior context) picking up where this session left off.
> **Date:** 2026-06-06 (last updated end of session, after final review pass)
> **Status:** Phase 1 + Phase 2 SHIPPED to production. Founding-member Premium grant SHIPPED. Click-tracking SHIPPED. Reengagement cron false-positive bug FIXED. Domain allowlist widened. Ready for June 15 launch from the backend perspective.

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

## 2. What landed (full commit history this session)

### Backend (`ahavah-api`, branch `ahavah/main`, all commits pushed to `github.com/techbasesolutions/duolicious-backend`)

Commits since the plan (`f924af0`), oldest first. All 26 are shipped on prod:

**Phase 1 — capture + email blast (Tasks 1-13):**
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

**Phase 1 ship + post-Phase-1 polish (after the blast went out):**
```
6f61082  docs: handover for next-agent pickup of beta-referrals Phase 2
7616bc5  cron(betareengagement): exclude beta testers who already have a person row
4eb9a0d  handover: document docker compose production-override trap
```

**Phase 2 — credit firing (Tasks 14-19):**
```
ce1018a  service/referrals: _credit_one helper (idempotent +5 credit)
4577198  service/referrals: credit_pending_for_invitee (graduated → credited)
e6cfb00  service/referrals: credit_pending_for_inviter (drain escrow on inviter graduate)
3498fe2  post_finish_onboarding: drive referral credits in both directions
ae483a7  GET /referrals/me + service.referrals.get_my_stats
```

**June 15 readiness (Premium + click tracking + handover):**
```
9900f5e  founding-member: grant 6 months of Premium on finish_onboarding
62fb317  referral-click: log /i/[code] hits to a new table
c8b6ecd  handover: TL;DR update — Phase 2 + founding-member grant + click tracking SHIPPED
```

Tags pushed: `phase1-referrals-blast-sent`, `phase2-referrals-credits-live`, `june15-readiness-and-click-tracking`.

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
0700829  i/[code]: fire-and-forget click log to backend                ← later REPLACED by d527c33
d527c33  i/[code]: await the click log instead of fire-and-forget     ← supersedes 0700829
```

The `0700829` fire-and-forget click logger NEVER ACTUALLY LANDED CLICKS in Vercel Node runtime — `void fetch + keepalive: true` got killed before completing. `d527c33` switched to `await fetch` with 800ms timeout; that DID work end-to-end on prod. If you ever try fire-and-forget HTTP from a Vercel Node Route Handler, expect this trap and use Next 16's `after()` or just `await`.

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

**Phase 2 (live now) wires** after `Q_FINISH_ONBOARDING` inside `post_finish_onboarding`:
```
credit_pending_for_invitee(tx, invitee_email, invitee_person_uuid)
   ├─ flip the row's status pending → graduated (in same api_tx)
   ├─ look up inviter's person row by normalized_email OR email
   ├─ if inviter has a person row:
   │    └─ _credit_one(tx, inviter_uuid, referral_id)
   │       ├─ idempotency pre-check via metadata->>'referral_id'
   │       └─ service.tokens.credit(+5, reason='referral', metadata={referral_id})
   │    └─ flip referral row graduated → credited
   └─ if inviter has NOT graduated yet, leave at 'graduated' for later drain

credit_pending_for_inviter(tx, inviter_email, inviter_person_uuid)
   └─ drain: every referral WHERE inviter_email=X AND status='graduated'
       → _credit_one for the inviter + flip to credited
```

**June-15-readiness work (live now) — see §3a below for full topology.**

## 3a. June-15-readiness shipping

Beyond Phase 1+2, three more pieces shipped this session:

### Founding-member 6-month Premium grant
- `service.entitlements.grant_founding_member_if_eligible(person_id, email)` is called from `post_finish_onboarding` AFTER the api_tx commits.
- Eligibility: email is in `beta_signup` (not unsubscribed) OR has a completed `waitlist_signup` row (non-empty `answers` jsonb).
- Grants the canonical `'premium'` entitlement with `subscription_expires_at = NOW() + 183 days` via the existing `entitlements.grant()` API (which opens its own tx — so caller MUST NOT hold one when calling).
- Promised in `emails/waitlist_welcome.py:51` ("Founding member perk: six months of Premium free at launch") and the `/resources/updates/waitlist-open-building-toward-launch.mdx` page. 26 waitlist + 24 beta signups received this promise.
- Idempotent via `grant()`'s "already_has" check + LATER-wins expiry comparison.

### Referral-link click tracking
- Migration `0025_referral_link_click.sql` creates `referral_link_click` table with `(id, code, inviter_email, well_formed, created_at, user_agent_class, user_agent)`. Indexes on `(code, created_at)`, `(inviter_email, created_at) WHERE NOT NULL`, and `(created_at DESC)`.
- Backend: `POST /referral-click` in `service/api/referral_click_route.py`. Public, no auth, swallows all exceptions, always returns `{ok: true}`. Classifies UA into `mobile / desktop / bot / unknown` via substring match (`bot`, `spider`, `crawler`, `preview`, `facebookexternalhit`, `twitterbot`, etc.).
- Frontend: `/i/[code]` Route Handler `await`s the POST with 800ms `AbortSignal.timeout`. **Adds 400-800ms latency to every /i/ redirect** — accepted tradeoff because `void fetch + keepalive` was empirically NOT honored by Vercel's Node runtime.

### Reengagement cron false-positive fix
- `service/cron/betareengagement/__init__.py` `_Q_PICK` now includes `AND NOT EXISTS (SELECT 1 FROM person p WHERE p.email = b.email)` so beta testers who skipped the waitlist demographic survey but completed real onboarding via /auth/sign-up aren't flagged as incomplete.

### Signup domain allowlist widening
- `AHAVAH_SIGNUP_ALLOWED_DOMAINS` on `/opt/ahavah-api/.env.production` is now `techbaseltd.com,gmail.com,yahoo.com,aol.com,outlook.com,proton.me,axxess.co.za,retznest.com,icloud.com` (covers all 7 cohort domains + icloud which had a recent waitlist signup).
- Backup at `/opt/ahavah-api/.env.production.bak.before-cohort-domains-2026-06-06`.

## 4. Production state right now (end of session)

| Thing | Count / value |
|---|---|
| beta_signup rows | 24 |
| beta_signup with referral_code | 20 (16 cohort + 4 new today, the 4 new haven't been emailed yet) |
| beta_signup with referral_intro_sent_at | 19 (18 from blast + 1 test send) |
| waitlist_signup rows | 31 |
| waitlist_signup with completed answers | 26 |
| person rows | 1 (`harrigan.tennyson@gmail.com`, name "Ehud", uuid `1ea2acc2-...`) |
| referral rows | 0 (none clicked + signed up yet) |
| referral_link_click rows | 0 (clean after smoke test) |
| token_ledger 'referral' rows | 0 |
| reengagement_sent_at stamped | 2 (harrigan + jpjbraden, both pre-fix) |
| Unsubscribes | 0 |

`jpjbraden@gmail.com` has a referral_code minted but `referral_intro_sent_at IS NULL` AND empty waitlist answers AND no person row. If they ever complete waitlist onboarding, the next blast `--all` will include them (the filter is "completed onboarding AND not previously emailed"). The reengagement cron will NOT re-nudge them (send-once flag is stamped from 2026-06-03).

## 5. How to monitor inbound clicks + attributions + credits

```bash
SSH='ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27'
PSQL='docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c'

# Per-inviter full funnel: clicks → signups → credited
$SSH "$PSQL \"
SELECT bs.email AS inviter,
       COUNT(DISTINCT rc.id) AS clicks,
       COUNT(DISTINCT r.id)  AS signups,
       COUNT(DISTINCT r.id) FILTER (WHERE r.status='credited') AS credited
  FROM beta_signup bs
  LEFT JOIN referral_link_click rc ON rc.inviter_email = bs.email
  LEFT JOIN referral r              ON r.inviter_email  = bs.email
 WHERE bs.referral_intro_sent_at IS NOT NULL
 GROUP BY 1
 ORDER BY clicks DESC NULLS LAST;\""

# Click stream (last 24h, filter out bots):
$SSH "$PSQL \"
SELECT code, inviter_email, user_agent_class, LEFT(user_agent, 60), created_at
  FROM referral_link_click
 WHERE created_at > NOW() - INTERVAL '24 hours'
   AND user_agent_class <> 'bot'
 ORDER BY created_at DESC;\""

# Referral rows by status (live cohort health):
$SSH "$PSQL \"
SELECT status, COUNT(*) FROM referral GROUP BY 1 ORDER BY 2 DESC;\""

# Token ledger 'referral' rows (every +5 credit lands here):
$SSH "$PSQL \"
SELECT person_id, delta, metadata->>'referral_id' AS referral_id, created_at
  FROM token_ledger
 WHERE reason = 'referral'
 ORDER BY created_at DESC LIMIT 20;\""

# Founding-member Premium grant audit:
$SSH "$PSQL \"
SELECT p.email,
       p.entitlements,
       p.subscription_expires_at,
       (p.subscription_expires_at - NOW()) AS time_left
  FROM person p
 WHERE 'premium' = ANY(p.entitlements)
 ORDER BY p.sign_up_time DESC;\""
```

## 6. What's the next iteration after this session

Phase 1 + Phase 2 + Premium grant + click tracking + cron fix are all SHIPPED. The biggest remaining items:

### June 15 launch (9 days out from this handover's date)
- App-readiness (FE): PROJECT-STATUS.md last said 17/40 screens. The core sign-up → onboarding → /map flow works (harrigan completed it on 2026-06-04). What's incomplete: R5 four-state coverage, filters drawer, voice recording, in-chat image picker, block/report, verification tiers, settings sub-pages, subscription mgmt, help pages.
- Send the `beta_launch` email when ready: `ssh ... "docker exec ahavah-api-api-1 python -m emails.send_beta_launch --all"` (already built, dry-run-safe).
- Toggle `AHAVAH_SIGNUPS_OPEN=true` on the droplet env to open signup beyond the allowlist (currently `false`).

### Deferred from the original plan (NOT shipped, may never be needed)
- `<ReferralCard>` FE component on the /waitlist completion screen — surfaces the user's own code + share button + funnel inline. Spec exists in the plan as Task 18 step but was deferred; Phase 1's email blast covers the introduction. Build only if you want an in-app pre-launch share surface.
- `POST /referrals/code` public endpoint for the pre-launch card — also deferred, depends on the card.

### Review-pass findings — all addressed in this session

The first review pass surfaced 8 issues. All are now resolved (mostly via the small follow-up commits at the end of the session):

| Issue | Resolution | Commit |
|---|---|---|
| `POST /referral-click` had no rate limiting | Added 60/min per-IP `_click_log_limit`. Per-IP is weak against the Vercel-egress legitimate-traffic pathway but useful as defense-in-depth against direct-curl abuse. | `4019fa5` |
| `credit_pending_for_invitee` had an unused `invitee_person_uuid` parameter | Dropped the param + updated the single call site. Docstring now explicitly notes the credit goes to the inviter (looked up from the referral row), not the invitee. | `ff16e75` |
| Founding-member eligibility check was asymmetric (`beta_signup.unsubscribed_at` filtered, waitlist's wasn't) | Removed the unsubscribe filter from both. Founding-member status is about WHEN you joined; unsubscribing from marketing emails shouldn't void six months of Premium that someone earned by joining early. | `8828a12` |
| `from datetime import timedelta` was at line 232 (mid-file) | Moved to top-of-file imports block. | `8828a12` |
| Click logging added 400-800ms latency on every `/i/<code>` redirect | Switched to Next 16's `after()` from `next/server` — the canonical pattern for post-response work in Route Handlers. Per docs, runs even on redirect, doesn't block, doesn't make the route dynamic. Click logging is now invisible to user-perceived latency. | `769ffca` (FE) |
| No unit tests for `_classify_ua` | Added `tests/test_referral_click.py` with 23 parameterized assertions covering empty/None, 10 bot signatures, 3 mobile signatures, 3 desktop signatures, and one priority test for bot-check-before-mobile-check. DB-touching pieces remain integration-tested only; documented why in the test file's docstring. | `5fb4748` |
| POSTGRES_PASSWORD "drift" | Phantom issue — re-tracing the incident showed there was no actual drift, just env substitution drift from missing docker-compose flags. The ALTER USER I ran during diagnosis was a harmless red herring. Documented in §7.6. | — |
| API outage (~5 min) during allowlist update | Process documentation already in §7.6. No code change. | — |

If any of these resolutions surfaces a regression in production, the relevant commit + section above should make rollback obvious.

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
