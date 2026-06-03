# Deferred security items — 2026-06-03 audit

Items from the multi-agent security audit (2026-06-03) that were intentionally NOT fixed in patches P1-P3 because they either (a) need user input we don't have, (b) are breaking changes to the public API, or (c) require multi-hour redesigns. Each item has the exact ask needed to unblock it.

Status legend:
- 🟢 ready — code change defined; needs only the input listed.
- 🟡 needs design — partial path forward; needs an architectural decision.
- 🔴 blocked — requires external infra we don't control from here.

---

## 1. 🔴 Bearer token → HttpOnly cookie migration (audit Frontend #1)

**Today.** `ahavah-web/src/lib/api-client.ts` stores the long-lived session bearer in `localStorage`. Single reflected XSS exfiltrates it. The header comments at `api-client.ts:5-7` still claim it's an `httpOnly` cookie — stale.

**To unblock.** Multi-hour rewrite touching both halves:
- Backend: issue `Set-Cookie: duo_session=<token>; HttpOnly; Secure; SameSite=Lax; Domain=ahavah.app; Path=/; Max-Age=2592000` on `/check-otp` success. Accept the cookie in `decorators.require_auth()` alongside (or instead of) the Bearer header. Mint a separate short-lived ticket for the chat WebSocket (it can't use HttpOnly cookies for cross-port WSS auth).
- Frontend: drop `Authorization` header path in `api-client.ts`; keep `credentials: "include"`. Add a `/auth/whoami` ping on app boot to check session validity (cookie isn't readable by JS).
- Adds CSRF surface — need to either (a) require SameSite=Lax (already enforced by browsers for top-level navigation; safe for our pattern) or (b) add a per-session CSRF token in a header for state-changing requests.

**Estimate:** half-day backend + half-day frontend + chat WS ticket flow.

---

## 2. 🔴 HTTPS to droplet (`http://167.71.93.27:5000`) — audit Infra #5

**Today.** Vercel rewrite at `ahavah-web/vercel.json:5` proxies `/api/*` → `http://167.71.93.27:5000`. User → Vercel is TLS, but Vercel-egress → DigitalOcean NYC3 is plaintext across the public internet. Bearer tokens, photo URLs, chat content all observable. Ports 5000/5442/5443/8080 are also bound to `0.0.0.0` on the droplet, so anyone on the internet can hit the API directly.

**To unblock.** Stand up nginx + Let's Encrypt on the droplet, then:
- Add a DNS A/AAAA for `api.ahavah.app` → `167.71.93.27` / IPv6.
- Run `certbot --nginx -d api.ahavah.app -d chat.ahavah.app` (chat needs its own cert for WSS on `5443`).
- nginx config: TLS 1.3 only, HSTS, proxy_pass to `localhost:5000` (and `localhost:5443` for WSS).
- Update `ahavah-web/vercel.json` + `next.config.ts` rewrites to `https://api.ahavah.app`.
- UFW: drop external access to `5000/5442/5443/8080`; only `443` + `80` (LE renewal) public.

**Note.** `compose.production.yml:21-23` already flags this as a Phase W TODO. Tracked.

**Estimate:** 1-2 hours including DNS propagation.

---

## 3. 🟢 Cloudflare Turnstile (or equivalent) on public POSTs — audit Email #2

**Today.** `/request-otp`, `/waitlist`, `/beta-tester`, `/feedback` (sans honeypot) have no CAPTCHA. The new per-recipient rate limit shipped in P2 mitigates inbox-flood but doesn't stop a low-volume scraper farming the disposable-domain DB or testing email-exists timing.

**To unblock — pick one and provide the secret:**
- **Cloudflare Turnstile** (recommended — invisible/managed): create a widget at https://dash.cloudflare.com/?to=/:account/turnstile, give me the site key + secret key. I'll embed the widget on the four forms and verify the token server-side.
- **hCaptcha** (similar UX, costs nothing): same pattern.
- **Honeypot only** (cheapest): just copy the `website`-hidden-field pattern from `/feedback` to the other three. No third-party dep, weaker but free.

**Estimate after secrets:** 1 hour.

---

## 4. 🟢 SSH host-key pinning + dedicated deploy user — audit Infra #8, #9

**Today.** `deploy-ahavah.yml:62-63` uses `StrictHostKeyChecking=no` + `UserKnownHostsFile=/dev/null` — accepts any host key. BGP hijack of the droplet IPv6 → MITM → stolen deploy SSH key. Also: deploys as `root`.

**To unblock — needs your droplet access:**
- Run on droplet: `ssh-keyscan -t ed25519 ::1` (or whatever the canonical IPv6 is). Paste the output into a new GitHub secret `DEPLOY_HOST_KEY`. I'll wire it into the workflow.
- Optional separate hardening — create a `deploy` user on the droplet with docker group + a sudoers entry for the migration `psql` step + git pull. Add the deploy key to `/home/deploy/.ssh/authorized_keys`. Set the `DEPLOY_USER` secret to `deploy`. I'll switch the workflow over.

**Estimate after `DEPLOY_HOST_KEY`:** 10 minutes.

---

## 5. 🟡 `/request-otp` response unification (audit Auth #4)

**Today.** Four distinct codes (200 / 400 disposable / 403 closed / 461 banned) + timing differences leak enumeration data (which IPs banned, which emails banned, which domains are flagged disposable, whether a domain is allowlisted).

**Why deferred.** Unifying to "always 200 + session_token" is the cleanest fix BUT it degrades UX — the waitlist wizard currently shows "Disposable email" inline. Hiding that error means typo'd disposable addresses fail at the OTP step instead, which is more confusing for legit users.

**To unblock — pick the trade:**
- **Option A — fully unify** (close enumeration, degrade UX): /request-otp always returns 200 + session_token, internally short-circuits SMTP for invalid/banned/disposable, with a fixed delay budget (~250ms) to mask timing.
- **Option B — keep 4xx codes, mask timing only**: still leak enumeration via response code but stop timing-based detection. Less protection, no UX hit.
- **Option C — accept current behavior** as the threat model is low (closed beta, small allowlist).

**Estimate:** 30 min once decided.

---

## 6. 🟡 Admin destructive GET → POST (audit Auth #7)

**Today.** `/admin/ban/<token>` + `/admin/delete-photo/<token>` are plain GET with one-shot tokens. Theoretical risk: Gmail's link-warmer / Microsoft SafeLinks / corporate URL scanners prefetch the URL and trigger the action before the admin clicks. Tokens are atomically consumed via the DELETE-RETURNING pattern so a SECOND fetch is a no-op — meaning the first prefetcher fires the action.

**Why deferred.** Mitigations exist:
- The tokens are short-lived (a few hours) so the prefetch window is tight.
- Admin email lives only in `admin@techbaseltd.com` inbox — not a heavily-scanned address.
- No reported false-positive bans yet.

**To unblock — pick the trade:**
- Convert `GET /admin/ban/<token>` to "GET returns a confirmation HTML page with a POST form; POST performs the action." 30 min change.
- Or just rotate to longer-random + shorter-TTL tokens.

**Estimate:** 30 min for the form-split.

---

## 7. 🔴 DMARC / SPF / DKIM verification (audit Email open question)

**Today.** `.env.production:24` comment claims "DKIM+SPF verified in Resend" but no DNS records are checked into the repo. Without `p=quarantine` (or stricter) DMARC, spoofed Ahavah mail will land in user inboxes (and conversely, our legitimate sends will be more spam-prone).

**To unblock — out-of-band DNS work:**
- Run `dig TXT ahavah.app` and `dig TXT _dmarc.ahavah.app` and `dig TXT resend._domainkey.ahavah.app`. Paste output. I'll diagnose what's missing and produce the exact records to add at the registrar.
- Then add DMARC `v=DMARC1; p=quarantine; rua=mailto:dmarc@ahavah.app; pct=100`.

**Estimate:** 15 min after DNS output.

---

## 8. 🔴 Inbound MX for `admin@ahavah.app` etc.

**Today.** Multiple emails advertise `mailto:admin@ahavah.app` and `support@ahavah.app` and `feedback@ahavah.app`. No inbound MX in the repo — replies likely vanish.

**To unblock — out-of-band DNS:**
- Pick a forwarder: **Cloudflare Email Routing** (free, easy — point MX at `*.mx.cloudflare.net`, set up rule `admin@ahavah.app → admin@techbaseltd.com`), Google Workspace, ImprovMX.
- Run `dig MX ahavah.app`. If empty: pick a provider, I'll write the records.

**Estimate:** 20 min after provider chosen.

---

## 9. 🟢 BIMI logo for sender avatar (deferred until DMARC + trademark)

**Today.** No BIMI record. Gmail/Apple Mail show generic avatar.

**To unblock — sequence:**
1. Move DMARC to `p=quarantine` or `p=reject` (item #7).
2. Register the Ahavah monogram trademark with USPTO/EUIPO (out of scope — months of legal process).
3. Buy a Verified Mark Certificate from DigiCert (~$1.5k/yr, requires trademark).
4. Then I can wire SVG Tiny PS + DNS TXT.

**Estimate:** weeks, not minutes — defer until post-launch + trademark filed.

---

## 10. 🟢 Pin GHA actions to commit SHAs (audit Infra MED)

**Today.** `actions/checkout@v3`, `actions/setup-python@v5` etc. pinned to floating tags. Compromise of the upstream tag = arbitrary code on our CI runner.

**Why deferred.** Low priority — `test.yml` runs on `pull_request` with fake secrets, `deploy-ahavah.yml` uses no third-party actions, `publish.yml` is workflow-dispatch only.

**To unblock — busywork:**
- I can pin everything to SHAs in a single PR if you want it.
- Or enable Dependabot for action updates and accept the ongoing PR noise.

**Estimate:** 30 min once you say go.

---

## 11. 🟡 Replace `requirements.txt` floating versions with `requirements.lock`

**Today.** Most of `ahavah-api/requirements.txt` is unpinned or floor-pinned (`flask`, `stripe>=11.0`, etc.). Each `docker build` re-resolves to current PyPI; a yanked or compromised release lands on next deploy with no review.

**To unblock — design call:**
- Use `pip-tools` to generate `requirements.lock` and commit. Install in Docker from the lock. Adds a "regenerate lock" step to the upgrade workflow.
- Or move to `uv` / `poetry` for proper lock management.

**Estimate:** 1-2 hours including the docker-compose tweak + first dependabot run.

---

## 12. 🟢 Verify GitHub repo visibility (audit Privacy open question)

**Today.** Both repos point to `github.com/techbasesolutions/*` — but `gh` CLI isn't available in this environment to confirm `--visibility private`. Backend's pre-fork commits are by definition public via upstream `duolicious/duolicious-backend`.

**To unblock:**
- You confirm both are private via the GitHub UI (one click).
- If either is public, run `gh repo edit --visibility private` and rotate any secrets that ever lived in commits since fork.

---

## Already shipped (P1 / P2 / P3 reference)

For completeness — the items below are DONE and live in production at the listed commits. Cross-reference for any future audit:

- `b796afb` (P1) — deploy + env hygiene + send-suppression + Next.js security headers + JSON-LD escape + PII log scrub + GHA permissions
- `3b073bf` (P2) — OTP attempt counter + session wipe on delete/email-change + per-recipient rate limit + pre-launch gate ordering + IDN/multi-@ normalize + atomic waitlist upsert + jsonb shape validation + outbox-pattern cron + CITEXT-equivalent CHECK constraints + beta_signup person_id FK + /me unauth email-strip + generic 500
- `fd7a88c` (P3) — `unsubscribed_at` columns + `/u/<token>` route + suppression in send_* + cron + List-Unsubscribe headers + Resend override prod boot guard + pendingdeletion inbox/waitlist cleanup
