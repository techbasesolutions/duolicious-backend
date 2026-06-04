# Ahavah security audit — full work log

**Date:** 2026-06-03 → 2026-06-04
**Scope:** Full-stack security pass across `ahavah-api` (backend) + `ahavah-web` (frontend) + infra (droplet, DNS, GHA, Vercel)
**Trigger:** User invoked `/karpathy-guidelines` + `/security-review` and asked for "a gamut of tests on this project its code base, database, repo, all its moving parts"
**Outcome:** 7 commits across 2 repos shipped + verified live. All HIGH/CRITICAL findings addressed or explicitly documented in `DEFERRED-SECURITY.md`. DNS hardened (SPF/DMARC/Google DKIM/Resend DKIM all live). HTTPS end-to-end (Vercel→droplet was plaintext, now `https://api.ahavah.app`). CAPTCHA layer shipped zero-config; activates by dropping in Cloudflare keys (see `CLOUDFLARE-TURNSTILE-SETUP.md`).

---

## Index — what shipped in what commit

| Commit | Repo / branch | Description |
|---|---|---|
| `b796afb` | `ahavah-api` / `ahavah/main` | P1 — quick-win deploy + env hygiene + send-suppression + PII log scrub |
| `5b92532` | `ahavah-web` / `master` | P1 — security headers + `/admin/*` proxy gate + `safeJsonLd()` |
| `3b073bf` | `ahavah-api` / `ahavah/main` | P2 — OTP attempt counter + session wipe + per-recipient limit + IDN normalize + atomic waitlist + jsonb shape + outbox cron + migration 0022 + `/me` strip |
| `fd7a88c` | `ahavah-api` / `ahavah/main` | P3 — migration 0023 + `/u/<token>` unsubscribe + List-Unsubscribe headers + Resend override boot guard + pendingdeletion cleanup |
| `8fe392c` | `ahavah-api` / `ahavah/main` | P4 docs — initial DEFERRED-SECURITY.md |
| `91a3d40` | `ahavah-api` / `ahavah/main` | infra — GHA actions SHA-pinned + SSH host-key pin + publish.yml deleted |
| `f868128` | `ahavah-web` / `master` | infra — Vercel rewrites flipped to `https://api.ahavah.app` |
| `52d85d5` | `ahavah-api` / `ahavah/main` | P5 — honeypot + Turnstile scaffold + admin GET→POST split |
| `a30640c` | `ahavah-web` / `master` | P5 — honeypot field + Turnstile widget + wired into 5 forms |
| `621049c` | `ahavah-api` / `ahavah/main` | P6 — sign-out-everywhere + stale session purge + `/skip` rate fix + change-email limit + firehol warn + Reply-To plumbing + email html-escape + deploy concurrency safer + Cloudflare doc |
| `bf789d8` | `ahavah-api` / `ahavah/main` | hotfix — firehol guard relaxed (was crashing prod boot because the bypass is intentionally enabled) |
| `e96fe82` | `ahavah-web` / `master` | gitignore `desktop-extra.jsx` |

Out-of-band changes (not in commits):
- 4 DNS TXT records added to `ahavah.app` zone via Vercel DNS API
- 1 GitHub secret added (`DEPLOY_HOST_KEY`)
- 1 Vercel env var updated (`NEXT_PUBLIC_CHAT_WS_URL`)
- 1 droplet env var unset (`DUO_RESEND_FROM_OVERRIDE`)
- 1 nginx cert expanded (api.ahavah.app + chat.ahavah.app)
- 2 production deploys (forced api container recreate after env changes)

---

## Phase 0 — multi-agent audit

Six parallel subagents, each given a scoped read-only audit prompt:
1. **Backend auth / session / abuse** — service/person, decorators, antiabuse
2. **Backend data integrity** — migrations, FK/cascade, races, jsonb shape, SQL injection
3. **Frontend security** — proxy, NEXT_PUBLIC_ leakage, XSS sinks, cookies, CSP
4. **Deploy + infra** — compose, GHA, SSH, Vercel, backups, dep hygiene
5. **Email + abuse** — SMTP paths, From spoofing, unsubscribe, DMARC/SPF/DKIM, rate limits
6. **Privacy + retention + repo hygiene** — Phase W deletion, PII in logs, git history secrets

Each agent returned a markdown report with severity-tagged findings (`CRIT/HIGH/MED/LOW`), file:line citations, reproducer steps, and one-line fix recommendations. Consolidated into a single severity-by-area matrix and prioritized top-10.

Tally: **3 CRIT / 17 HIGH / 19 MED / 17 LOW** findings.

**One audit error caught + corrected:** the data-integrity agent flagged "migrations not run" as CRITICAL based on reading `database/initapi.py` only. Actually `deploy-ahavah.yml:77-85` iterates `migrations/*.sql` and runs each via `psql -f` on every deploy — so the migrations DO run. Surface-corrected before any code was written based on the bad premise.

---

## Phase 1 — quick wins (commits `b796afb` + `5b92532`)

**Backend (`b796afb`):**

| Change | File | Audit ref |
|---|---|---|
| `cron` service added to GHA build + up | `.github/workflows/deploy-ahavah.yml` | Infra #1 (CRIT — the betareengagement cron we'd shipped earlier wasn't actually running because GHA only rebuilt `api chat`) |
| Migration 0021 backfill date-pinned to `< 2026-05-27` | `migrations/0021_beta_reengagement_sent_at.sql` | DI #8 (the unguarded `WHERE reengagement_sent_at IS NULL` would suppress every legit future beta if the migration re-ran) |
| `.env.production.template` — add `POSTGRES_PASSWORD`, `SESSION_TOKEN_SECRET`, `DUO_SMTP_PASS` doc, `DUO_USE_RESEND_API`, `DUO_RESEND_FROM_OVERRIDE`, `DUO_DISABLE_FIREHOL`, `REDIS_PASSWORD`, `VAPID_*`. CORS default flipped from `*` to explicit ahavah.app list. | `.env.production.template` | Infra #3 |
| `${VAR:?required}` guards on `POSTGRES_PASSWORD`, `SESSION_TOKEN_SECRET`, `DUO_SMTP_PASS` so compose fails loud instead of silently substituting empty | `docker-compose.production.yml` | Infra #3 |
| `emails/base.py` — `is_suppressed_send()`, `suppressed_sql_pattern()`, `mask_email()` helpers. Default suppress list: `example.com`, `techbaseltd.com` (env-overridable) | `emails/base.py` | Email #3 (audit found team test addresses would get hit by bulk sends) |
| Routed `is_suppressed_send()` through 10 email send helpers + the betareengagement cron's `_Q_PICK` (`ILIKE ANY(%(suppressed)s)`) | `emails/{beta_welcome,beta_launch,reengagement,reengagement_reminder,waitlist_admin,waitlist_welcome,send_*}.py` + `service/cron/betareengagement/__init__.py` | Email #3 |
| PII scrub — replaced raw `{email}` in stdout with `mask_email()` (`j***e@example.com` form) | `service/cron/{betareengagement,autodeactivate2}/__init__.py`, `service/person/__init__.py:879` | Privacy #3 |
| `notifications` cron — log only `person_uuid + flags`, not the full row that includes push tokens | `service/cron/notifications/__init__.py` | Privacy #4 |
| `vm/docker/.env` → `vm/docker/.env.example` (`git mv`) | `vm/docker/.env` | Infra #2 (file was tracked with `DUO_DB_PASS=password` placeholder; future operator edit could become a real-secret commit) |
| Top-level `permissions: contents: read` on all 3 GHA workflows | `.github/workflows/*.yml` | Infra MED |

**Frontend (`5b92532`):**

| Change | File | Audit ref |
|---|---|---|
| Global CSP + `X-Frame-Options: DENY` + `nosniff` + `Referrer-Policy: strict-origin-when-cross-origin` + HSTS + Permissions-Policy headers via `headers()` in `next.config.ts` | `next.config.ts` | Frontend #2 |
| `/admin/:path*` added to pre-launch proxy matcher | `src/proxy.ts` | Frontend #3 |
| New `safeJsonLd()` helper — escapes `<`, `>`, `&`, U+2028, U+2029 so embedded `</script>` can't break the JSON-LD island | `src/lib/json-ld.ts` | Frontend LOW (latent today; matters when JSON-LD content moves to MDX/CMS) |
| Replaced `JSON.stringify(...)` with `safeJsonLd(...)` at 10 sites — `layout.tsx`, `faq/page.tsx`, `landing-faq.tsx`, `biblical-polygyny`, `faith-marriage-abroad`, `messianic-matchmaking`, 4 `resources/` pages | 10 .tsx files | same |

**ESLint pre-commit caught:** initial `safeJsonLd` used a `.replace(/<LS>/g, ...)` literal regex where the line separator chars made ESLint flag "unterminated regex literal." Rewrote using `String.fromCharCode(0x2028)` + `.split().join()`. Lesson: don't put U+2028 / U+2029 inside JS source even in a string regex — use the char-code form.

GHA deploy verification: `26917201761` green at 22:39Z, 5m16s.

---

## Phase 2 — backend hardening (commit `3b073bf`)

**OTP brute-force surface (audit Auth #1 — CRIT):**

- Migration 0022 adds `duo_session.otp_attempts INT DEFAULT 0`.
- `Q_INCREMENT_OTP_ATTEMPTS` increments + nulls the OTP after 5 wrong guesses.
- `Q_MAYBE_SIGN_IN` + `Q_MAYBE_DELETE_ONBOARDEE` now also `SET otp = NULL, otp_expiry = NOW(), otp_attempts = 0` on success — OTP is one-shot.
- `post_check_otp`: on miss, increments counter; returns `"Too many attempts. Request a new code."` once locked.

**Session lifecycle (audit Auth #2 — HIGH):**

- `Q_DELETE_DUO_SESSIONS_FOR_PERSON` + `Q_DELETE_DUO_SESSIONS_FOR_PERSON_EXCEPT`.
- `delete_or_ban_account` self-delete branch wipes every duo_session for the person — stolen-pre-delete token loses access immediately instead of after the 7-day pending-deletion grace.
- `change_email_verify` wipes every OTHER duo_session for the person (keeps caller's current session) so stolen-pre-email-change token loses access.

**Public POST hardening:**

- `shared_recipient_limit = limiter.shared_limit("5 per hour; 20 per day", scope="recipient", key_func=request_body['email'])` — per-target rate limit so N-IP botnets can't inbox-bomb an arbitrary victim with verified-sender Ahavah mail (audit Email #1 — HIGH).
- `make_decorator` extended to accept a list of limiters so `shared_recipient_limit` stacks on `shared_otp_limit` / `beta_limit`.
- Applied to `/request-otp`, `/waitlist`, `/beta-tester`.
- `post_request_otp`: moved `SIGNUPS_OPEN` allow-domain gate ABOVE firehol + disposable checks so pre-launch callers can't enumerate the disposable-domain table by probing (audit Auth #3 — HIGH).
- `decorators.validate()`: generic `"Internal server error"` on 500 instead of `str(e)` — stops file paths + lib versions leaking (audit Auth LOW).

**Email + domain normalization (audit Auth #3):**

- `_split_one_at()` — safely partitions on last `@` so malformed multi-@ inputs don't raise ValueError.
- `normalize_email()` now also IDN-ASCII-encodes the domain via `.encode('idna')` so allow-list comparisons can't be bypassed by a Cyrillic-`а` homoglyph (audit Auth #3).

**Data integrity (audit DI #2-#5, #7):**

- Migration 0022: `CHECK (email = lower(btrim(email)))` on `beta_signup` + `waitlist_signup`; cast `beta_signup.person_id BIGINT → INT` and add FK to `person(id) ON DELETE SET NULL`.
- `service/waitlist/__init__.py upsert()` — single-statement CTE with `FOR UPDATE` on the prev row. Two concurrent posts can no longer both observe `prev_complete=false` and both fire admin notices.
- `PostWaitlist.validate_answers()` — whitelist allowed keys, cap string length 256, list size 16, total payload 4KB.
- `service/cron/betareengagement` — outbox-pattern claim. Stamps `reengagement_sent_at` BEFORE the SMTP call inside a separate `api_tx` so concurrent ticks / process restarts can't double-send.

**PII leak (audit Auth #6):**

- `/me/<person_id>` unauth path strips `email` from response (audit found anyone with a UUID could harvest the email). `get_me()` gained `include_email=False` kwarg passed by `get_me_by_id`; authed `/me` keeps `include_email=True`.

GHA verification: `26917834379` green at 22:52Z, 5m18s.

---

## Phase 3 — email infra + privacy (commit `fd7a88c`)

**Unsubscribe stack (audit Email #4, #5, #6 — MED):**

- Migration 0023: `unsubscribed_at TIMESTAMPTZ` on `waitlist_signup` + `beta_signup`; partial indexes on the not-null case.
- `service/unsubscribe/__init__.py` — HMAC-signed token (`scope|email` signed with `SESSION_TOKEN_SECRET`), `make_url()`, `parse_token()`, `stamp_unsubscribed()`.
- `service/api/unsubscribe_routes.py` — public GET + POST `/u/<token>`. GET serves a branded confirmation page; POST is the RFC 8058 one-click endpoint Gmail/Yahoo hit automatically. Both stamp idempotently. Same confirmation regardless of whether the email matched (no list-membership leak).
- Send-time gates: all 4 bulk send scripts + the betareengagement cron's `_Q_PICK` now check `unsubscribed_at IS NULL`.

**SMTP headers (audit Email #4 — MED, Gmail/Yahoo bulk-sender requirement):**

- `Smtp.send()` gained `reply_to` + `list_unsubscribe` kwargs. Both SMTP and Resend-HTTPS paths set `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.
- Each marketing email's `_footer()` refactored to take email arg + render token-backed `/u/<token>` link.

**Resend override prod guard (audit Email LOW):**

- `smtp/__init__.py` prints a `WARNING:` line at import when `DUO_ENV=prod` and `DUO_RESEND_FROM_OVERRIDE` is non-empty. (Later in P6 the override was unset; this guard now logs nothing in prod, as intended.)

**Phase-W deletion cleanup (audit Privacy #1, #2):**

- `service/cron/pendingdeletion` when hard-deleting a person row also `DELETE FROM inbox WHERE luser = uuid` (XMPP message-receipt table — no FK, was orphaning); `DELETE FROM waitlist_signup` + `DELETE FROM beta_signup` by email.

GHA verification: `26918292522` green at 23:02Z, 5m29s.

---

## Phase 4 — DEFERRED-SECURITY.md (commit `8fe392c`)

12-item document covering everything intentionally NOT done in P1-P3. Each item carries the exact ask needed to unblock.

---

## CLI-deferred items round (commits `91a3d40` + `f868128`)

User asked: "everything you can do via cli do." Inventoried which deferred items I could execute without input.

**Inspected current state via `nslookup` + `curl` + Vercel API:**

- DNS authoritative for `ahavah.app` is on Vercel (`ns1.vercel-dns.com`, `ns2.vercel-dns.com`).
- `api.ahavah.app` + `chat.ahavah.app` A records ALREADY pointed at `167.71.93.27`.
- MX records were already on `smtp.google.com` (Google Workspace).
- `resend._domainkey.ahavah.app` DKIM TXT already present (Resend already verified for ahavah.app).
- CAA records permit `letsencrypt.org`.
- **Both repos were `PUBLIC`** — flagged for user decision (irreversible-in-spirit; not auto-flipped).

**DNS records added via Vercel API:**
```
TXT ahavah.app         "v=spf1 include:_spf.google.com include:amazonses.com ~all"
TXT _dmarc.ahavah.app  "v=DMARC1; p=quarantine; pct=100; rua=mailto:admin@techbaseltd.com; ruf=mailto:admin@techbaseltd.com; aspf=r; adkim=r"
```
Verified via `nslookup` propagation.

**nginx + Let's Encrypt on droplet:**
- Inspected `/etc/nginx/sites-enabled/ahavah` — already configured for `api.ahavah.app` + `chat.ahavah.app` server_names + cert covered them all.
- Ran `certbot --nginx --expand --cert-name 167-71-93-27.nip.io ...` — was a no-op (cert already valid). HTTPS was actually already alive at `https://api.ahavah.app/health`.

**Frontend rewrite flipped (`f868128`):**
- `vercel.json` + `next.config.ts` rewrites: `http://167.71.93.27:5000` → `https://api.ahavah.app`.
- Vercel env var `NEXT_PUBLIC_CHAT_WS_URL` updated via API to `wss://chat.ahavah.app/chat-ws`.

**Backend hardening (`91a3d40`):**
- `gh secret set DEPLOY_HOST_KEY` with `ssh-keyscan` output for both IPv4 + IPv6 droplet addresses.
- `deploy-ahavah.yml` flipped to `StrictHostKeyChecking=yes` + `UserKnownHostsFile=~/.ssh/known_hosts` (was `=no` + `/dev/null`). Pinning prevents BGP/DNS-hijack MITM.
- `test.yml` actions pinned to commit SHAs: `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5` (v4) and `f43a0e5ff2bd294095638e18286ca9a3d1956744` (v3), `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065` (v5).
- `publish.yml` deleted — was workflow_dispatch-only, referenced nonexistent GCP secrets in this fork.

GHA verification: `26919690792` green at 23:37Z, 4m55s (first deploy using the pinned host key).

---

## Phase 5 — captcha + admin GET→POST (commits `52d85d5` + `a30640c`)

User: "Captcha - free and accessible option / you decide the rest."

**Decision rationale on captcha:**
- Picked **Cloudflare Turnstile** for: WCAG 2.1 AAA accessibility, free + unlimited, no CC required, invisible by default (no visual puzzles), privacy-first (no third-party tracking).
- Since I didn't have your Cloudflare account, shipped in TWO layers:
  1. **Honeypot field** on `/request-otp`, `/waitlist`, `/beta-tester` — works NOW, fully accessible, matches existing `/feedback` pattern.
  2. **Turnstile-ready code** — backend verifies if `TURNSTILE_SECRET_KEY` is set, frontend renders widget if `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is set; both no-op when env vars are blank. Drop in keys → activates.

**Decisions on "the rest":**
- ✅ Admin GET→POST split — DO IT (small, audit-recommended)
- ⏸ `/request-otp` unify — KEEP CURRENT (closed beta + allowlist already gates)
- ⏸ Bearer → cookie — DEFER (multi-hour breaking redesign)
- ⏸ `requirements.lock` — DEFER (drift risk vs. shipping speed)
- ✅ Update DEFERRED-SECURITY.md

**Backend (`52d85d5`):**

- New `service/antibot/__init__.py` — `is_honeypot_hit()` + `verify_turnstile()`. `verify_turnstile()` returns True when secret unset (no-op); POSTs to `https://challenges.cloudflare.com/turnstile/v0/siteverify` and rejects on failure when set.
- `duotypes/__init__.py` — `PostRequestOtp`, `PostWaitlist`, `PostBetaTester` each gain `website` (honeypot) + `turnstile_token` optional fields.
- Handlers — honeypot hits return success-shaped 200 silently (no detection signal); Turnstile verify runs after the existing pre-launch gate.
- `.env.production.template` + compose — `TURNSTILE_SECRET_KEY=${VAR:-}` with template doc.

**Admin GET→POST (audit Auth #7 — MED):**
- `/admin/ban-link/<token>` + `/admin/delete-photo-link/<token>` still GET (admin email link target). They now serve a CONFIRMATION FORM that POSTs to `/admin/ban/<token>` + `/admin/delete-photo/<token>`.
- `/admin/ban` + `/admin/delete-photo` now ONLY accept POST. Gmail prefetchers / Microsoft SafeLinks / corporate URL scanners that GET the URL can no longer fire the ban/delete by prefetch.
- New `_confirm_form_html()` helper in `service/person/__init__.py` with `html.escape`d token + dark stylesheet.

**Frontend (`a30640c`):**

- `src/lib/antibot.ts` — `useTurnstile()` hook loads Cloudflare script + renders invisible widget; stable null token when env unset. `antibotPayload()` helper.
- `src/components/app/honeypot-field.tsx` — `sr-only` field with `autoComplete=off + tabIndex=-1 + aria-hidden`.
- `src/components/app/antibot-fields.tsx` — `forwardRef` wrapper bundling HoneypotField + invisible widget container into one drop-in element with `payload()` + `reset()` ref handle.
- `src/lib/{auth-otp,waitlist,beta}.ts` — signatures extended with optional `antibot` payload.
- Wired into 5 forms + 1 card: `src/app/page.tsx`, `waitlist/page.tsx`, `auth/sign-in/page.tsx`, `auth/sign-up/page.tsx`, `onboarding/verify-email/page.tsx`, `components/app/beta-tester-card.tsx`.

**ESLint pre-commit caught (twice):**
1. Initial honeypot field used `style={{position:"absolute", left:"-10000px", ...}}` — flagged by `no-restricted-syntax` (project bans inline sizing/spacing styles). Switched to Tailwind `sr-only` class.
2. `<div ref={turnstile.ref}>` flagged by `react-hooks/refs` ("Cannot access refs during render") even though it's idiomatic. Destructured at hook call: `const { ref: turnstileRef, token: turnstileToken, reset: resetTurnstile } = useTurnstile()`. Rule satisfied.

GHA verification (backend): `26922133333` green at 00:38Z, 5m34s.

---

## Out-of-band — Google DKIM (during P5)

User pasted Google Workspace DKIM record mid-stream:
```
google._domainkey TXT v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOC...
```
Added via Vercel DNS API. Verified via `nslookup` propagation.

---

## Phase 6 — audit-tail cleanup (commit `621049c`)

User: "the others that require choices just choose the best option" + "scratch bimi" + "what else did the audit reveal?"

Walked back through the 6 audit reports and enumerated everything still latent.

**Decisions:**
- Bearer → cookie: defer with plan (multi-hour, deserves own session)
- `/request-otp` unify: keep current (Option C)
- SSH deploy user: defer (operator coordination needed)
- `requirements.lock`: defer (no `pip-compile` locally; doing it right needs a Docker-based resolution)
- `DUO_RESEND_FROM_OVERRIDE`: UNSET (apex SPF + DKIM + DMARC now live — per-template addresses authenticate properly)

**Backend code changes:**

- `Q_PURGE_STALE_UNSIGNED_SESSIONS` — called at top of `post_request_otp` to drop accumulated `signed_in=FALSE` rows for the same normalized email (audit Auth #5 leftover — pre-auth bearers were accumulating unbounded).
- `POST /sign-out-everywhere` + `post_sign_out_everywhere()` — wipes EVERY duo_session for the person (audit Auth #8 — users had no self-service way to revoke a stolen token).
- `/skip` rate limit fix (audit Auth #11) — inverted; report path stays at `1/5s + 20/day`, skip-without-report now `200/hour` per IP + account (was the default 60/min).
- `/account/change-email-verify` — new `shared_limit("5 per hour")` per account (audit Auth #10 — tightened from default 60/min on a 24-bit OTP).
- Firehol bypass — loud warning at import (audit Auth #12). Initially also raised in prod; rolled back in hotfix (see below).
- `emails/feedback.py` — `reply_to=email` so admin Reply routes back to the user (audit Email #7).
- `service/person/_send_otp` — `reply_to=hello@` so OTP replies reach a human (audit Email #9).
- `emails/reengagement.py` + `reengagement_reminder.py` — `html.escape(quote(email))` on the waitlist deep-link (audit Email #11 — defense in depth).
- `deploy-ahavah.yml` concurrency `cancel-in-progress: false` (audit Infra #10 — was killing in-flight deploys mid-`docker compose up`).
- `test.yml` — comment block noting the repo-Settings fork-PR gate that still has to be set in the UI (audit Infra #7).
- `.gitignore` — exclude `docs/superpowers/` (agent scratch).

**Frontend (`e96fe82`):** `.gitignore` — exclude `desktop-extra.jsx`.

**Out-of-band — droplet env:**
```bash
ssh root@167.71.93.27
cd /opt/ahavah-api
cp .env.production .env.production.bak.20260603-235754  # safety backup
sed -i 's|^DUO_RESEND_FROM_OVERRIDE=.*|DUO_RESEND_FROM_OVERRIDE=|' .env.production
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up -d --force-recreate api
```
After 6-second cold-start the `/health` endpoint returned 200.

**Docs:**
- `docs/CLOUDFLARE-TURNSTILE-SETUP.md` — verified-accurate-2026-06-03 walkthrough using live Cloudflare docs. Exact dashboard URL, exact widget mode (`Invisible`), exact env-var names + apply commands, three smoke tests.

GHA: `26923150834` FAILED at 00:59Z — see hotfix below.

---

## Hotfix `bf789d8` — firehol guard

The P6 firehol guard was over-zealous:
```python
if DUO_DISABLE_FIREHOL is true and DUO_ENV == 'prod':
    raise RuntimeError(...)
```
But `DUO_DISABLE_FIREHOL=true` IS intentionally set on the droplet (memory note: firehol child process was OOM-killed under load on the s-2vcpu-4gb tier; 2GB swap was added 2026-05-12 but the bypass stayed in).

API container failed to boot. Rolled back to print-only warning. Pushed `bf789d8`, GHA `26923384394` green at 01:10Z, 4m40s. `/health` returns 200.

**Lesson:** I added the audit-suggested guard without checking the operator's reason for the existing env value. Should have checked the droplet env first.

---

## DNS state (verified live via `nslookup`)

```
ahavah.app          TXT  v=spf1 include:_spf.google.com include:amazonses.com ~all
_dmarc.ahavah.app   TXT  v=DMARC1; p=quarantine; pct=100; rua=mailto:admin@techbaseltd.com; ...
resend._domainkey.ahavah.app  TXT  v=DKIM1 ... (Resend transactional)
google._domainkey.ahavah.app  TXT  v=DKIM1 ... (Google Workspace inbox)
send.ahavah.app     TXT  v=spf1 include:amazonses.com ~all (Resend bounce subdomain)
api.ahavah.app      A    167.71.93.27
chat.ahavah.app     A    167.71.93.27
MX                       smtp.google.com (Google Workspace)
```

Email auth posture is now full: SPF passes, DKIM signs (both Resend + Google selectors live), DMARC enforces `p=quarantine`.

---

## Live verification summary

| Surface | Test | Result |
|---|---|---|
| `https://api.ahavah.app/health` | GET | 200 `status: ok` |
| Pre-launch gate (public) | `POST /request-otp` with `random@gmail.com` | 403 "Signups are not open yet" |
| Pre-launch bypass (allowlist) | `POST /request-otp` with `admin@techbaseltd.com` | 200 + session_token |
| Honeypot | `POST /request-otp` with `website: "evil"` | 200 + dummy session_token (silent drop) |
| Admin destructive — GET | `GET /admin/ban/x` | 405 Method Not Allowed |
| Unsubscribe — bad token | `GET /u/invalidtoken` | 400 + branded HTML |
| `/sign-out-everywhere` (unauth) | `POST /sign-out-everywhere` | 400 (auth required, expected) |
| Frontend → backend transport | Vercel rewrite | `https://api.ahavah.app` (was `http://167.71.93.27:5000`) |
| Chat WSS | `NEXT_PUBLIC_CHAT_WS_URL` | `wss://chat.ahavah.app/chat-ws` (was `ws://`) |
| SSH host-key pin | next deploy after pin | `26919690792` ✅ used `StrictHostKeyChecking=yes` end-to-end |

---

## What's still on the table — see DEFERRED-SECURITY.md

After P6, 6 items remain:

1. **Bearer → HttpOnly cookie migration** — multi-hour, deserves own session
2. **`/request-otp` response unification** — kept current; revisit at launch
3. **Turnstile activation** — drop in the 2 Cloudflare keys (see `CLOUDFLARE-TURNSTILE-SETUP.md`)
4. **SSH deploy user root → deploy** — needs operator-side `useradd` + sudoers
5. **`requirements.lock`** — needs Docker-based pip-compile to match prod resolution
6. **Repo visibility flip** — both repos are PUBLIC; needs explicit user go-ahead

Plus the firehol fix itself is now a known gap: IP blocklist is OFF on prod because firehol OOMs. Long-term fix: bigger droplet or replace firehol with a lighter blocklist.

---

## Files created this round

```
ahavah-api/
  docs/
    CLOUDFLARE-TURNSTILE-SETUP.md             [P6 docs]
    DEFERRED-SECURITY.md                      [P4, updated P5]
    SECURITY-AUDIT-CHANGELOG-2026-06-03.md    [this file]
  migrations/
    0022_auth_hardening.sql                   [P2]
    0023_unsubscribe.sql                      [P3]
  service/
    antibot/__init__.py                       [P5]
    api/unsubscribe_routes.py                 [P3]
    cron/betareengagement/__init__.py         [pre-audit, modified P1+P3+P6]
    unsubscribe/__init__.py                   [P3]

ahavah-web/
  src/
    components/app/
      antibot-fields.tsx                      [P5]
      honeypot-field.tsx                      [P5]
    lib/
      antibot.ts                              [P5]
      json-ld.ts                              [P1]
```

Total: 11 new files + ~30 modified files across both repos.

---

## Sequence of decisions (mostly mine)

1. **Multi-agent audit over single-agent audit** — 6 scoped subagents in parallel rather than one big context. Tradeoff: harder to merge findings but each scope was deep instead of shallow. Worth it.
2. **Per-Karpathy: state plan before executing each phase** — done for each of P1-P6.
3. **Catch audit error early** — the "migrations not run" CRIT was wrong (subagent didn't see the GHA migration loop). Surfaced this before committing code based on the false premise.
4. **Three pushes between phases** — smaller blast radius on rollback, lets each phase be independently verified.
5. **Zero-config CAPTCHA rollout** — ship the code paths now, activate by env var later. Lets the work land without waiting on the user to provision Cloudflare.
6. **Defer multi-hour breaking changes** (cookie migration, single-conn-per-worker pool) to their own sessions instead of half-shipping.
7. **Document everything left** in `DEFERRED-SECURITY.md` with exact unblock instructions, so the work is resumable cold.

## Sequence of mistakes (mine to fix)

1. **firehol guard regression** — added "refuse to bypass in prod" without checking the operator's existing setting. Broke prod boot. Hotfixed in `bf789d8`. Lesson: read the live env state before adding a guard against a value.
2. **Subagent attempted "migrations not run" CRIT** — caught, but it cost time before I noticed. Lesson: subagent claims about runtime behavior need a runtime check.
3. **ESLint regex literal with U+2028** — initial `safeJsonLd` parser-errored. Fixed inline. Lesson: don't put parser-hostile chars in source even inside string regex.
4. **ESLint `react-hooks/refs`** caught the inline `turnstile.ref` access during render. Fixed by destructuring. Lesson: project enforces a stricter ref rule than the React docs require — destructure at the hook call.
