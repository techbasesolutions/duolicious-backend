# Cloudflare Turnstile setup — verified accurate 2026-06-03

Step-by-step guide for activating Turnstile on Ahavah's `/request-otp`,
`/waitlist`, and `/beta-tester` endpoints. Implementation already shipped
in zero-config form — drop the keys in to activate.

## 1. Does this cost anything?

No. Cloudflare account signup is free and **no credit card is required**.
Turnstile itself is free, unlimited, privacy-first (no third-party
tracking), and invisible by default — exactly what we picked it for
(WCAG 2.1 AAA accessibility + zero cost).

Source: `https://www.cloudflare.com/plans/` ("Start building for free
— no credit card required") and the Turnstile docs make no mention of
any quota gating on the widget itself.

## 2. Create a Cloudflare account

1. Go to `https://dash.cloudflare.com/sign-up`.
2. Enter email + password. No payment method is collected on the Free plan.
3. Verify the email link Cloudflare sends.
4. You do **not** need to add `ahavah.app` as a Cloudflare-managed zone
   for Turnstile to work — DNS stays on Vercel where it is today.
   Turnstile only needs the hostname string allow-listed on the widget.

## 3. Add a Turnstile widget

Once signed in:

1. Open the Turnstile page directly:
   **`https://dash.cloudflare.com/?to=/:account/turnstile`**
   (Cloudflare's own deep link from their docs — resolves `:account`
   to your account ID automatically.)
2. Click **`Add widget`** (exact label).
3. Fill the form:
   - **Widget name** — descriptive. Use `ahavah-prod`. (Cloudflare
     recommends separate widgets per environment; you can add a
     second `ahavah-dev` widget later if you want.)
   - **Hostname management** — domains where the widget will be used.
     Add all three to be safe:
     - `ahavah.app`
     - `www.ahavah.app`
     - `ahavah-web.vercel.app` (for Vercel preview deployments)
   - **Widget Mode** — three options: `Managed`, `Non-Interactive`,
     `Invisible`. **Select `Invisible`.** This matches the frontend
     code which passes `size: "invisible"` to `window.turnstile.render`
     in [src/lib/antibot.ts:96](../ahavah-web/src/lib/antibot.ts).
     Invisible mode: visitors never interact with the widget and don't
     see any indication that a browser challenge is in progress.
4. **Do NOT add `localhost`** to the production widget. For local dev,
   use Cloudflare's test keys (section 5 footnote) — they work on any
   domain including localhost.
5. Submit. Cloudflare shows the **Sitekey** and **Secret key** on the
   next screen. Copy both before you navigate away — Cloudflare warns:
   "Copy your sitekey and secret key, and store the secret key securely."

## 4. Get the site key + secret key

After clicking Add widget:

- **Sitekey** — public, safe to embed in frontend JS. Goes into
  `NEXT_PUBLIC_TURNSTILE_SITE_KEY`.
- **Secret key** — private, server-side only. Goes into
  `TURNSTILE_SECRET_KEY`. Shown once; rotate from widget settings if lost.

Real keys begin with `0x...`. The `1x...` / `2x...` / `3x...` strings
mentioned below are Cloudflare's documented dummy keys for testing.

## 5. Drop the keys into Ahavah

Two separate places. Do **both**.

### 5a. Backend — `TURNSTILE_SECRET_KEY` on the droplet

The backend reads `TURNSTILE_SECRET_KEY` at process start
([service/antibot/__init__.py](../service/antibot/__init__.py) line 32).
When blank, `verify_turnstile()` returns `True` for every call (no-op
rollout). When set, the route returns 403 "Verification failed" on a
missing/invalid token.

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27
cd /opt/ahavah-api

# Edit .env.production — add the line at the bottom (replace the placeholder):
nano .env.production
# Add:
#   TURNSTILE_SECRET_KEY=0xABC...your-real-secret...

# Recreate ONLY the api container so it picks up the new env:
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  up -d --force-recreate api

# Verify it came up clean:
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  logs api --tail 50
```

### 5b. Frontend — `NEXT_PUBLIC_TURNSTILE_SITE_KEY` in Vercel

The frontend reads `NEXT_PUBLIC_TURNSTILE_SITE_KEY` at **build time**
([src/lib/antibot.ts:27](../ahavah-web/src/lib/antibot.ts)). Because it's
`NEXT_PUBLIC_*`, it's baked into the client bundle — you **must
redeploy** after changing it.

**Via dashboard (recommended):**
1. Vercel → ahavah-web → Settings → Environment Variables → Add New
2. Name: `NEXT_PUBLIC_TURNSTILE_SITE_KEY`
3. Value: `<sitekey>` (the public one from section 4)
4. Environments: **Production** checked (Preview optional)
5. Save → Deployments tab → ... → Redeploy the latest production deployment

**Via Vercel CLI:**
```powershell
# In d:\Antigravity\ahavah-web
"0xABC...your-real-sitekey..." | vercel env add NEXT_PUBLIC_TURNSTILE_SITE_KEY production --token <vercel-token>
vercel --prod --token <vercel-token>
```

### Dev / local testing note

Drop these Cloudflare dummy keys into a local `.env.local` to exercise
the full flow without provisioning real keys:

| Purpose | Key |
|---|---|
| Frontend always-passes sitekey | `1x00000000000000000000AA` |
| Backend always-passes secret | `1x0000000000000000000000000000000AA` |
| Backend always-fails secret (to test the 403 path) | `2x0000000000000000000000000000000AA` |

These work on any hostname including `localhost`.

## 6. Verify it's working

The implementation contract:
- Backend ([service/antibot/__init__.py:47-49](../service/antibot/__init__.py)):
  if `TURNSTILE_SECRET_KEY` is unset → all requests pass.
- Backend (line 50-51): if secret is set AND token is missing →
  `verify_turnstile()` returns `False` → route returns **403 "Verification failed"**.
- Backend (line 52-66): POSTs to
  `https://challenges.cloudflare.com/turnstile/v0/siteverify` with form-
  encoded `secret` + `response` (+ optional `remoteip`). Reads
  `success: true/false` from the JSON response.
- Frontend ([src/lib/antibot.ts:64](../ahavah-web/src/lib/antibot.ts)):
  loads `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit`
  and renders an invisible widget that produces a one-shot token. Cloudflare
  tokens are valid for 300 seconds and can only be redeemed once.

**Smoke tests** — run these *after* both env vars are set + both deploys are live:

### Test 1 — missing-token → 403

```bash
curl -i -X POST https://api.ahavah.app/request-otp \
  -H "Content-Type: application/json" \
  -d '{"email":"smoketest@example.com"}'
```

Expect `HTTP/1.1 403` with body containing `"Verification failed"`. This
proves the backend secret is loaded and `verify_turnstile()` is gating
the route.

### Test 2 — valid-token → 200

Open `https://ahavah.app` in a fresh browser, devtools Network tab,
request an OTP through the real UI. Verify:
- `challenges.cloudflare.com/turnstile/v0/api.js` was fetched (sitekey
  wired correctly).
- The OTP POST body includes a non-null `turnstile_token`.
- Response is `200`.

### Test 3 — invisible (no visible widget)

Visit the OTP page in an incognito window. There should be no checkbox,
no Cloudflare badge, no visible challenge — just the form.

### If something fails

| Symptom | Likely cause |
|---|---|
| Test 1 returns `200` not `403` | Backend secret didn't get loaded — re-check `.env.production` and the `--force-recreate api` step ran clean |
| Test 2 POST has `turnstile_token: null` | Frontend sitekey is missing — re-check the Vercel env var landed under **Production** and a redeploy actually happened (Vercel won't apply env changes to existing deployments) |
| Test 3 shows a visible widget | Widget Mode is `Managed` or `Non-Interactive` not `Invisible` — edit the widget in dashboard, change mode |
| `challenges.cloudflare.com/turnstile/v0/api.js` 4xx in Network tab | Hostname mismatch — add the actual hostname (e.g. preview deploy URL) to the widget's Hostname management list |
