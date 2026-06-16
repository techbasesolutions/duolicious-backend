# Ahavah Launch — Design Spec

**Date:** 2026-06-15
**Status:** Approved design (brainstorming). Next: implementation plan (writing-plans).

## Goal

Take Ahavah from closed preview to live: purge the test accounts, retire the
preview host, and email all 53 waitlist registrants a magic link that logs them
in, pre-fills their account from their waitlist answers, and drops them into
onboarding to finish (name, DOB, photos).

## Decisions (locked)

- **Account creation:** lazy — the claim link creates the account only on first click (no pre-created ghost profiles).
- **Landing:** link logs in → onboarding wizard at the first missing step, demographics pre-filled. Full app unlocks when essentials (name/DOB/photos) are done.
- **Recipients:** all 53 `waitlist_signup` rows (0 unsubscribed).
- **signup.ahavah.app:** fully remove the Vercel domain alias.
- **Purge:** snapshot DB first, then hard-delete.

## Component 1 — Claim token + `POST /claim`

- **Token:** reuse `service/unsubscribe`'s HMAC-over-`scope|email` (key = `SESSION_TOKEN_SECRET`), new scope `"claim"`. `make_url("claim", email, WEB_BASE_URL)` → `https://ahavah.app/claim/<token>`. Unforgeable; emailed to the recipient's own inbox.
- **Endpoint `POST /claim {token}`** (new, in `service/api` + a `service/claim` module):
  1. `parse_token` → email (reject invalid → 400).
  2. Load `waitlist_signup.answers` for that email (may be `{}` for the 13 email-only rows — that's fine, no pre-fill).
  3. **Upsert an `onboardee`** for the email, pre-filling from answers:
     - `gender_id` ← lookup `gender.name = answers.sex` (skip if no match).
     - `coordinates` ← `get_country_location(answers.country)` representative location (the helper built for the country→map fix). Skip if country missing/unresolvable.
     - `ahavah_extra` ← JSONB merge of `{assembly, intent, family, relocate_willing}` present in answers.
     - Leave `name`, `date_of_birth`, photos NULL (the user finishes these).
     - Idempotent: if a `person` already exists for the email, do NOT create an onboardee — just mint a session against that person (covers double-clicks / pre-existing accounts).
  4. **Mint session:** `secrets.token_hex(64)` + `sha512` → INSERT `duo_session` (email, `session_token_hash`, `person_id` = existing person or NULL, `signed_in = TRUE`), mirroring the admin "log in as user" path (`service/api/admin/users_action_routes.py:285`). `signed_in=TRUE` so the FE treats them as authenticated.
  5. Return `{ session_token, onboarded: <person exists?> }`.
- **Verify-at-build (value formats):** confirm `answers.sex` strings match `gender.name`, and `answers.country` format matches what `get_country_location` expects (ISO2). The e2e test (below) catches mismatches.

## Component 2 — Claim landing page (FE)

- New route `src/app/claim/[token]/page.tsx`: on mount, `POST /claim {token}` → store `session_token` in `localStorage["ahavah.session-token"]` + set `ahavah.authed` cookie (same as `api-client` login), then route to onboarding (first missing step) if not onboarded, else to the app. Error state (bad/expired token) → friendly "this link didn't work, request a new one" with support contact.
- No new onboarding/gating logic — `onboarded=false` + the existing `firstMissingStepFor` gate route them through the wizard.

## Component 3 — Launch email

- New `emails/launch.py` on the brand shell (`emails/base.py`): chip, new title PNG ("We're live" — render via `scripts/render-title-png.mjs`), lime CTA button = per-recipient claim link, copyable plaintext URL below it, unsubscribe footer (`make_url("waitlist", email, …)`).
- **Copy:** celebratory open; honest "these are early days, you'll hit rough edges"; explicit feedback ask ("reply or tap Report when something breaks"); invite-your-friends line (existing referral links). No em-dashes.
- New `emails/send_launch.py`: iterate all `waitlist_signup` rows, skip `is_suppressed_send` domains, mint a `claim` token + build the email per recipient, send via `make_aws_smtp().send(...)`. **`--dry-run` default**: render + print the recipient list + a sample HTML, send nothing. `--send` to actually send.

## Component 4 — Purge

- **Backup first:** `pg_dump` of `duo_api` → DO Spaces `ahavah-photos-prod/backups/pre-launch-<date>.sql`.
- **Delete (by the 6 emails / ids 9,12,14,15,16,17):**
  - `DELETE FROM person WHERE id IN (…)` — CASCADEs to ~30 tables (duo_session, photo, liked, ahavah_match, token_ledger, messaged, search_preference_*, push_subscription, notification_preference, profile_view, …). `beta_signup.person_id` → SET NULL (row preserved).
  - `DELETE FROM onboardee WHERE normalized_email IN (…)` — independent table, no cascade.
  - `DELETE FROM duo_session WHERE email IN (…)` — catch any onboardee-stage sessions (person_id NULL) not covered by the person cascade.
- **Preserve:** `waitlist_signup`, `beta_signup` (rows, now person_id NULL), referral tables — untouched.
- **Verify after:** `SELECT count(*) FROM person` = 0; `SELECT count(*) FROM waitlist_signup` = 53 (unchanged).

## Component 5 — Remove signup.ahavah.app

- Remove the `signup.ahavah.app` domain alias from the `ahavah-web` Vercel project (Vercel CLI/dashboard). `ahavah.app` + `api.ahavah.app` untouched. The launch link targets `ahavah.app`.

## Execution sequence (each destructive/outward step gated on explicit OK)

1. Build claim token + endpoint + FE page. **End-to-end test** with a seeded test waitlist row: click link → onboardee created + pre-filled + session minted → lands in onboarding → finish → person created. *No email until this passes.*
2. Build the launch email + send script; **dry-run** (render + recipient list).
3. **[GATE]** Backup → purge the 6 accounts → verify.
4. **[GATE]** Remove signup.ahavah.app.
5. **[GATE]** Review the dry-run render + recipient list → send to all 53.

## Testing / success criteria

- Claim e2e: a test `waitlist_signup` row with known answers → `POST /claim` returns a working session token; the FE lands in onboarding with sex/country/ahavah_extra pre-filled; `/finish-onboarding` promotes to person.
- Idempotency: claiming twice does not duplicate the onboardee/person.
- Purge: person count 0, waitlist count 53 after.
- Email: dry-run renders on the brand shell, no `#70f`/placeholder, 53 recipients listed, every link is a valid `claim` token.

## Out of scope

- New-user (non-waitlist) signup flow changes; post-launch unauthed-visitor behavior of the proxy gate. "Invite others" is email copy + existing referral links only.
