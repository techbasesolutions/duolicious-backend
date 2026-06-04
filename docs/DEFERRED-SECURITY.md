# Deferred security items — audit cycle 2026-06-03

Items from the 2026-06-03 multi-agent security audit that remain after patches P1–P5. Each item carries the exact ask needed to unblock it.

Status legend:
- 🟢 ready — code change defined; needs only the input listed.
- 🟡 needs design — partial path forward; needs an architectural decision.
- 🔴 blocked — requires external infra we don't fully control from here.

Cross-reference: see commit history for the shipped fixes.

---

## STILL DEFERRED

### 1. 🔴 Bearer token → HttpOnly cookie migration (audit Frontend #1)

**Today.** `ahavah-web/src/lib/api-client.ts` stores the long-lived session bearer in `localStorage`. Single reflected XSS exfiltrates it.

**To unblock.** Multi-hour rewrite touching both halves: backend issues `Set-Cookie: duo_session=…; HttpOnly; Secure; SameSite=Lax` on `/check-otp`, accepts cookie in `decorators.require_auth()`; frontend drops the `Authorization` header path; WebSocket needs a short-lived ticket flow because cross-port WSS can't read HttpOnly cookies. Schedule a half-day session.

---

### 2. 🟡 `/request-otp` response unification (audit Auth #4)

**Today.** Four distinct codes (200 / 400 disposable / 403 closed / 461 banned) + timing differences leak enumeration data.

**Why deferred.** Picked Option C: closed beta + allowlist already limits the threat. Revisit at launch — Option A (unify to 200 + constant-time budget) closes enumeration but degrades UX (typo'd disposable addresses fail at the OTP step instead of inline).

---

### 3. 🟡 Cloudflare Turnstile keys

**Today.** Code paths are wired and zero-config:
- Backend: `service/antibot/__init__.py` verifies tokens when `TURNSTILE_SECRET_KEY` is set; no-op otherwise.
- Frontend: `src/lib/antibot.ts` + `src/components/app/antibot-fields.tsx` render the invisible widget when `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is set; render nothing otherwise.
- Honeypot field is active everywhere regardless.

**To activate.** Create a free widget at https://dash.cloudflare.com/?to=/:account/turnstile (Cloudflare → Turnstile → Add site → invisible mode), then:
- Set `NEXT_PUBLIC_TURNSTILE_SITE_KEY` in Vercel (techbase-hq → ahavah-web → Settings → Environment Variables).
- Set `TURNSTILE_SECRET_KEY` in the droplet's `.env.production`.
- Redeploy backend (rebuild api container) + frontend (push or redeploy in Vercel).

No code changes required.

---

### 4. 🟡 Switch deploy SSH user from `root` to a dedicated `deploy` (audit Infra)

**Today.** Compromise of `DEPLOY_SSH_KEY` GitHub secret = full root on droplet.

**To unblock.** On droplet: `useradd -m -G docker deploy`, copy authorized_keys for the ahavah-deploy public key, add a sudoers entry for the migration `psql` step + `docker compose`. Then update `DEPLOY_USER` GitHub secret to `deploy`. ~30 min plus operator access.

---

### 5. 🟡 `requirements.lock` via pip-tools

**Today.** Most of `ahavah-api/requirements.txt` is unpinned or floor-pinned. Each docker build re-resolves to current PyPI; yanked/compromised releases would land silently.

**Why deferred.** Risk vs reward not worth running through a deploy cycle while shipping these audits. When the cadence settles, generate `requirements.lock` with `pip-compile` and switch the Dockerfile to install from the lock.

---

### 6. 🚨 Repo visibility — BOTH PUBLIC

**Today.** `github.com/techbasesolutions/duolicious-backend` (forked from public upstream `duolicious/duolicious-backend`) and `github.com/techbasesolutions/ahavah-web` are both `PUBLIC`.

**To unblock.**
- Decide whether to flip private: `gh repo edit techbasesolutions/ahavah-web --visibility private` and same for backend.
- If flipping, also `git log -p` for any `sk_live` / `re_` / `AKIA` / `BEGIN PRIVATE KEY` etc. that may have ever been committed before the visibility flip is observed. Rotate anything found.

---

### 7. 🟢 Pre-launch warning if `DUO_RESEND_FROM_OVERRIDE` is set in prod

Already implemented in P3 — the backend now prints a `WARNING:` line at module import when `DUO_ENV=prod` and `DUO_RESEND_FROM_OVERRIDE` is non-empty.

**Current droplet state.** `DUO_RESEND_FROM_OVERRIDE=noreply@ahavah.app`. With apex SPF + DKIM (Resend selector) + DMARC now live, this is no longer technically necessary — every send could use its intended From: address (`hello@`, `waitlist@`, `feedback@` …) instead of being rewritten to `noreply@`. Decision: keep override (uniform sender, simpler deliverability story) or remove (better per-template branding, requires verifying each From: is configured in Resend). I left it on; flip when comfortable.

---

### 8. 🟡 BIMI logo (sender avatar in Gmail / Apple Mail)

Multi-month sequence: register Ahavah monogram trademark (USPTO/EUIPO), buy a Verified Mark Certificate from DigiCert (~$1.5k/yr, requires registered trademark), then I can wire SVG Tiny PS + DNS TXT. Defer until post-launch + trademark filed.

---

## DONE (since the original deferred list)

All of these have shipped:

- ✅ **HTTPS to droplet** (Infra #2). nginx + LE cert covers `api.ahavah.app` + `chat.ahavah.app`. Vercel rewrites flipped to `https://api.ahavah.app`. `NEXT_PUBLIC_CHAT_WS_URL` updated to `wss://chat.ahavah.app/chat-ws`.
- ✅ **DMARC + SPF + Google DKIM** at `ahavah.app` (live, verified via `nslookup`).
- ✅ **CAPTCHA** — Honeypot field on `/request-otp` + `/waitlist` + `/beta-tester` (zero-key, active now). Cloudflare Turnstile scaffolding ready — drop the env vars in to activate.
- ✅ **Admin GET → POST split** for `/admin/ban` and `/admin/delete-photo`. The `…-link` GET endpoints serve a confirmation form that POSTs to the destructive endpoint; link-warmers can no longer prefetch the action.
- ✅ **GHA SSH host-key pinning** via `DEPLOY_HOST_KEY` secret + `StrictHostKeyChecking=yes`.
- ✅ **GHA actions pinned to SHAs** for `actions/checkout`, `actions/setup-python`.
- ✅ **Dead `publish.yml`** removed.
- ✅ **Repo visibility** — verified (both public — see item #6 above for the action).

For earlier shipped work see commits since `b796afb` on `ahavah/main`.
