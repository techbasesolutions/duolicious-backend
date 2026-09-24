Linear: TEC-945

# Scheduled social posting with approval, and Threads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Ahavah's brand posts (the Claude Design sets, not member cards) on a schedule that never publishes without the owner's approval, and give Threads its own text-only schedule.

**Architecture:** Brand posts become rows in the queue that already carries member cards, so they inherit the thing this wave exists for: an operator approves, a cron publishes, a removal path takes it down. No new approval surface, no second publisher, no new state machine. Threads is a separate platform on the same rows, with its own schedule and its own text, published through the Threads API (a different host, a different app and a different token from the Facebook and Instagram path).

**Tech Stack:** Flask + psycopg 3 (`ahavah-api`), Next.js 16 admin with Vercel crons (`ahavah-admin`), Meta Graph API v26.0, Threads API v1.0 (`graph.threads.net`).

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` for the queue and consent machinery this reuses. Content and cadence: `docs/marketing/2026-09-19-social-content-cadence-research.md`. Voice and audience, binding on every caption: `ahavah-web/docs/design-briefs/ahavah-audience-and-voice.md`. Feast dates: `docs/marketing/feast-dates-2026.md`.

## Owner decisions (2026-09-20)

1. **Approval before posting.** Nothing publishes on a schedule alone. A scheduled brand post waits in `review` until an operator approves it in the Growth tab, exactly like a member card.
2. **Threads gets its own schedule and is text only.** No images, its own cadence, its own copy.

## What this is not

- **The Facebook Group stays manual.** Meta removed Groups publishing in April 2024. The plan produces a reminder, not an automation.
- **No new admin screen.** Brand posts appear in the Growth tab queue that exists. If the owner later wants a calendar view, that is a Claude Design brief and a separate wave.
- **No member data.** Brand posts carry no member name, photo or count that is not already public.

## Global Constraints

- Pushing `ahavah/main` (API) or admin `master` deploys production. Work on `social-scheduler` branches; deploy only after review and on the owner's go.
- Never nest `api_tx`. No literal `%` in psycopg SQL. The suite really commits: never hardcode a key.
- No em dashes on added lines; sentence case; no attribution trailers in any commit.
- Never echo a secret. The Threads token is handled exactly like the Page token: set in Vercel, never printed, never committed.
- Every caption is bound by the audience and voice reference. No Jewish framing, no rabbinic feast names, nothing from the list of community-splitting topics.
- Nothing promotional publishes on a Sabbath or a high day from the owner's calendar. Tabernacles gets warm content only.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 774). Admin: `node --test tests/*.test.mjs` (baseline 131), `npx tsc --noEmit`, `npx next build`.

---

### Task 1: A brand post is a queue row (API)

**Files:** `migrations/0053_brand_posts.sql`, `service/spotlight/queue.py`, `service/api/admin/spotlight_routes.py`, tests.

- [ ] Add `kind = 'brand'` to `KINDS`. A brand row has no `subject_person_id`, like a roundup, so the consent machinery has nothing to ask for and `consent_complete` must treat it as satisfied (verify, do not assume: read `dispatch.py` before changing anything).
- [ ] `POST /admin/growth/brand` (cron header or admin session, `growth_limit`): body `{slug, caption, image_base64, content_type, scheduled_for, platforms}`. `slug` is the business key, so a repeated call converges the way the weekly roundup does rather than creating a second post. Creates the row in `review`, not `scheduled`: approval is the owner's, per decision 1.
- [ ] The image goes through the same validation and storage as a member card (`validate_card_image`, `put_card_image`, content-hashed key).
- [ ] Tests: a brand row needs no consent; a duplicate slug converges; a row lands in `review`; a bad image is refused; the row publishes through the existing worker once approved.

### Task 2: The calendar, and what is due (admin)

**Files:** `ahavah-admin/content/social-calendar.json`, `src/lib/social-calendar.ts`, tests.

- [ ] One JSON file, one entry per post: `id`, `publishAt` (ISO, UTC), `channels` (`facebook`, `instagram`, `threads`), `image` (a path under the repo for the rendered artwork, absent for Threads), `captions` (per channel, because Instagram captions carry no working link and Threads is 500 characters), `pillar`, and `notes`.
- [ ] `dueEntries(now, calendar)`: entries whose `publishAt` has passed and which have no queue row yet. Pure function, tested.
- [ ] **The calendar is checked against the owner's feast dates at load.** An entry that falls on a Sabbath or a high day fails the test suite rather than the cron, with the date named. Read `ahavah-api/docs/marketing/feast-dates-2026.md`; keep the dates in one module so the next month's update is one edit.
- [ ] Tests: due and not due; an entry on a Sabbath is refused; a Threads entry with an image is refused; a caption over 500 characters on Threads is refused; every entry has a caption for every channel it names.

### Task 3: The queueing cron (admin)

**Files:** `src/app/api/growth/social-queue/route.ts`, `src/lib/social-queue.ts`, `vercel.json`, tests.

- [ ] A daily cron reads the calendar, finds what is due within the next 48 hours, and calls the Task 1 route to create each row in `review`. It never publishes. It is the thing that puts work in front of the owner early enough to be approved.
- [ ] Idempotent by `slug`, so a duplicate cron run creates nothing new (the same convergence the welcome path uses).
- [ ] The owner is told there is something to approve. Reuse the existing operator notification path if one exists; if not, say so in the report rather than inventing a channel.
- [ ] Tests: two runs create one row; an entry already queued is skipped; a failure on one entry does not stop the rest.

### Task 4: Threads, text only (admin + API)

**Files:** `migrations/0054_threads_platform.sql`, `service/campaigns/__init__.py` (`PLATFORMS`), `service/spotlight/queue.py`, `src/lib/publishing.ts`, `.env.example`, tests.

- [ ] **Owner step first, and it blocks this task:** a Threads app (Threads use case) at developers.facebook.com, with `threads_basic` and `threads_content_publish`, the owner added as a Threads tester, and a long-lived Threads user token (60 days, refreshable). Separate app, separate token, separate id from the Facebook Page. Sources: [Threads get started](https://developers.facebook.com/docs/threads/get-started), [Threads posts](https://developers.facebook.com/docs/threads/posts).
- [ ] Add `threads` to `PLATFORMS` and to the platform check constraint. **Read every query that assumes two platforms before changing it** (per-platform captions, the roundup's channels, `attach_platform_image`, `complete_render_if_ready`'s one-sha rule, the removal worker): a third platform that carries no image must not break the render completion rule. List each site checked in the report.
- [ ] Publish: `POST https://graph.threads.net/v1.0/{threads-user-id}/threads` with `media_type=TEXT` and `text`, then `POST /{threads-user-id}/threads_publish` with `creation_id`, waiting about 30 seconds between the two as the documentation recommends. 500 character limit. 250 posts per 24 hours. Same run deadline rules as the Graph path, and the same never-abandon-after-publish invariant.
- [ ] Token health: the daily check learns about the Threads token too, including its 60 day expiry, because an expired Threads token is silent otherwise.
- [ ] Env: `AHAVAH_THREADS_USER_ID`, `AHAVAH_THREADS_TOKEN`, `AHAVAH_THREADS_APP_ID`, `AHAVAH_THREADS_APP_SECRET` in `.env.example`, blank.
- [ ] Tests: a text post publishes through both steps in order; an image on a Threads row is refused; over 500 characters is refused before any call; a deadline stops it before the publish call, never after; a missing token reports unknown rather than failing the run.

### Task 5: Rendering the approved designs (admin)

**Files:** `src/lib/brand-render.ts`, `tests/`, the corrected Claude Design exports once they land.

- [ ] Render a design template to a 1080x1350 JPEG with the same renderer the member cards use (`next/og` plus sharp), so brand posts inherit the format Instagram accepts and the quality already proven.
- [ ] **Do not start this task until the corrected designs are pulled.** The current exports still carry the problems named in the social content brief.
- [ ] Rendered check: view every produced image at full size before it reaches the queue, and attach them to the report.

### Task 6: Review, deploy, and the first week

- [ ] Whole-branch review; one fix wave if needed.
- [ ] Deploy API then admin. Verify the crons, and that nothing publishes without an approval.
- [ ] Load the first four weeks from the research calendar, queue week one, and walk the owner through approving a post.

## Self-review record

- Owner decision 1 (approval) is structural, not a setting: a brand row is created in `review`, and only the existing operator approve moves it on.
- Owner decision 2 (Threads separate, text only) is enforced in the calendar tests and again in the publisher.
- Reuse over invention: no new approval surface, no second publisher, no new removal path.
- The riskiest task is 4, because a third platform touches code that has assumed two since Wave 1. It is deliberately last of the code tasks and carries an explicit audit step.
