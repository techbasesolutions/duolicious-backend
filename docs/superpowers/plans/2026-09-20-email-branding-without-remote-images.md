Linear: TEC-895

# Email branding that does not depend on the reader trusting a sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Ahavah email looks like Ahavah the moment it opens, even when the reader's mail client blocks remote images.

**Architecture:** The logo and the Ultra title travel inside the message as inline parts (`cid:`) instead of being fetched from ahavah.app. The mailer attaches them itself, by reading the `cid:` references out of the HTML it was handed, so no send site changes and no template changes beyond the URL they already ask for. A missing asset falls back to the remote URL, so the worst case is today's behaviour.

**Tech Stack:** Python `email.mime` (SMTP path) and the Resend HTTPS API (the path production actually uses), Flask API repo `ahavah-api`.

**Spec:** none; this is a defect. Evidence: 2026-09-20, the owner received a sign-in code email with no logo and no headline, in a fallback font, because his client had images allowed for `support@` but not for the address that email used. Immediate fix shipped (one sender for every member-facing email, commit a268513). This plan removes the dependency on that permission at all.

## Why inline parts

A mail client decides per sender whether to load remote images. Gmail blocks them for an unknown sender until the reader clicks. Every piece of Ahavah brand furniture in an email is a remote image, so a blocked sender means an unbranded email: no logo, no Ultra headline, body copy in whatever font the client picks. Inline parts are part of the message, so they render with no permission and no network call.

Cost: about 37 KB added to a typical email (two logo variants, two title variants). Against a 100 KB budget for Gmail clipping, and most of that budget is unused: the current templates run 6 to 8 KB of HTML.

## Global Constraints

- Pushing `ahavah/main` deploys production. Work on `email-inline-assets`; deploy after review.
- No em dashes on added lines; sentence case; no attribution trailers.
- Never break a send: every failure path in this work falls back to the remote URL rather than raising. An email that looks wrong is bad; an email that never arrives is worse.
- The mailer is used by every email in the product. Nothing in this plan changes recipients, subjects, copy or send timing.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 777).

---

### Task 1: The assets live where the API can read them

**Files:** `emails/assets/` (new), `tests/email_assets_manifest.txt`, `scripts/sync_email_assets.sh` (new), `.dockerignore` and the Dockerfiles if they exclude it.

- [ ] Copy the 65 PNGs from `ahavah-web/public/email/` into `emails/assets/`, about 952 KB total. They are brand assets, already served publicly, and the API cannot attach what it cannot read. Keep the filenames identical so a `cid:` and a URL name the same file.
- [ ] A small script re-syncs them and regenerates `tests/email_assets_manifest.txt`, so the two repos cannot drift.
- [ ] A test fails when an asset referenced by a template is missing from `emails/assets/`, and when `emails/assets/` and the manifest disagree.
- [ ] Confirm the images reach the running image: `docker exec ahavah-api-api-1 ls emails/assets | wc -l` after a build, recorded in the report.

### Task 2: The mailer attaches what the HTML asks for

**Files:** `smtp/__init__.py`, tests.

- [ ] Add one private helper: given the HTML body, find every `cid:<name>` reference, load `emails/assets/<name>`, and return the parts to attach. An asset that cannot be read is skipped and logged, never raised.
- [ ] **SMTP path:** wrap the existing `multipart/alternative` in a `multipart/related`, attach each image with `Content-ID: <name>` and `Content-Disposition: inline`. The alternative part stays exactly as it is, so a client with no image support sees what it sees today.
- [ ] **Resend path (what production uses):** send the same images in `attachments`, each with `content` (base64), `filename`, and `content_id` matching the `cid:`. Verify the field names against Resend's own API reference before writing them, and cite what you read in the report; do not trust this plan's spelling of them.
- [ ] Caching: read each asset once per process, not once per email.
- [ ] Tests: a body with two `cid:` references produces two inline parts with the right ids; a body with none produces a message byte-identical to today's; a missing asset sends the mail without it; the Resend payload carries the attachments in the documented shape.

### Task 3: The templates ask for inline assets

**Files:** `emails/base.py`, tests.

- [ ] `title_image()` and the logo emit `cid:<filename>` when the asset exists locally, and the current `https://ahavah.app/email/<filename>` when it does not. One switch, in one place, so every template inherits it.
- [ ] `alt` text stays exactly as it is: it is what a text-only client shows.
- [ ] The dark and light variants both go inline, so the `prefers-color-scheme` swap still works with no network.
- [ ] Tests: a rendered email references `cid:` for both logo variants and both title variants; the fallback path still emits the https URL when an asset is absent; the existing brand-shell guard still passes.

### Task 4: Prove it in a real inbox

- [ ] Send one sign-in code and one campaign email to the owner's address **with images blocked at the client**, and have him confirm the logo and headline appear. A screenshot of the rendered HTML is not proof here: the whole defect lives in what a mail client chooses to fetch.
- [ ] Check the received message size against Gmail's 102 KB clipping threshold and record it.
- [ ] Record in the report: message size before and after, and which client was used to verify.

### Task 5: Review and deploy

- [ ] Whole-branch review; one fix wave if needed.
- [ ] Deploy the API. Watch the first real sign-in code go out, confirm the outbox drains, and confirm no send failed.
- [ ] Update `feedback_never_hand_roll_brand_email_type` in memory: branding is inline, so a blocked sender is no longer a way for an email to look wrong.

## Self-review record

- The riskiest part is Task 2, because the mailer carries every email in the product. It is why the fallback is "send without the image" everywhere, and why Task 2's tests include "a body with no cid produces today's message".
- Task 1 duplicates assets across two repos. The alternative, fetching them over HTTP at send time, puts a network call in the path of every email and fails exactly when the network does. The sync script plus a drift test is the cheaper trade.
- Nothing here changes what any email says.
