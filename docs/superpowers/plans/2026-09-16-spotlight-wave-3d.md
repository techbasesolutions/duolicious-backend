# Community Spotlight, Wave 3d (pre-activation fixes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every defect the local acceptance run and the Meta documentation check found that would stop, or make unsafe, the first live post, so the owner's setup steps land on code that works.

**Architecture:** Branch `spotlight-wave-3d` in `ahavah-api`, `ahavah-web` and `ahavah-admin`, cut from the Wave 3c deploy heads. API changes stay additive for the admin app except where noted; the one breaking change (card approval now names its revision) is safe because `approvals_enabled` is `false` in production, so no member can reach that route during the deploy window. Deploy order: API, then web, then admin.

**Tech Stack:** Flask + psycopg 3 (REPEATABLE READ default), boto3 1.35.99 with botocore Stubber for tests; Next.js 16 (web, admin), `next/og` renderer, `sharp` for JPEG encoding; Meta Graph API.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md`. Evidence: `docs/superpowers/plans/2026-09-16-spotlight-acceptance-local.md` (section numbers below refer to it). Meta sources: the owner setup research (2026-09-16), cited inline.

## Scope

In, from the acceptance run's "Before the first live post" list:

| Item | Evidence | Task |
| --- | --- | --- |
| Approve of a rendered card always answers 503 | 5a | 1 |
| Stale tab records consent on a revision it never showed | 1a | 2 |
| Duplicate ticks create duplicate welcomes and E4s | 8a | 3 |
| Duplicate roundup calls answer 500 | 8a | 3 |
| Duplicate email submit answers 500 part way | 4d | 3 |
| 200-row listings starve old renders and overdue removals | 8c | 4 |
| Slow Graph overruns the 60 s budget with no checkpoint | 8d | 5 |
| No rollback or cron-pause runbook | 10d | 7 |

In, from the Meta documentation check:

| Item | Source | Task |
| --- | --- | --- |
| Instagram accepts JPEG only; the card is PNG | IG content publishing reference | 6 |
| `message` on `/{page-id}/photos` is deprecated, use `caption` | Page photos reference | 5 |
| `debug_token` needs an app access token, not the Page token | debug_token reference | 5 |
| Graph default `v21.0` expires 2027-01-21; current is `v26.0` | Graph changelog | 5 |
| `AHAVAH_PHOTO_HOSTS`, `AHAVAH_GROWTH_CRON_SECRET`, Meta app vars absent from env templates | repo | 7 |

**Out, stated plainly (owner decisions, not dropped silently):**

- A resumable background job for email cohort sends (F07's full remedy). Task 3 makes the duplicate submit answer cleanly and keeps it safe to retry; it does not move the send out of the HTTP request.
- A moderation action that withdraws a card (Lifecycle 2b): needs an owner ruling first.
- Roundup participant consent route: roundup tiles stay off (`roundup_tiles_enabled` false).
- A Growth tab button for recovering a missed Monday roundup (8b): the admin route exists; the surface needs a Claude Design brief.
- Everything the owner does in Meta, Vercel and the droplet, and the staging run.

**Ruling on the slow-Graph policy (8d), for the owner to overturn:** one row per publish run, a 45 s run deadline checked before each Graph step that can still be abandoned safely, and nothing checked after `media_publish` or `/photos` has been called. A row cut off before publishing is released as an ordinary not-attempted failure and retried next minute; a run cut off by the platform after publishing still parks the row for a human through the existing lease reaper, which never double posts (2c). Cost if wrong: at most one post per minute, which is far above Spotlight's volume.

## Global Constraints

- Pushing `ahavah/main` (API), web `master` or admin `master` deploys production. Work on `spotlight-wave-3d` branches only; deploy only after the whole-branch review is clean and the owner says go.
- Never nest `api_tx`. Fixtures before transactions. No literal `%` in psycopg SQL. The test suite really commits: never hardcode a key, receipt or request key.
- A concurrency test must actually race: two threads or processes against the real test database, each with its own `api_tx`, started together with a `threading.Barrier`. A test that calls the route twice in sequence does not prove convergence.
- A storage test must use botocore's real client shape (`botocore.stub.Stubber`), never a hand-written stub class that defines the method under test.
- No em dashes (U+2014) on added lines; sentence case; **no attribution trailers in any commit**.
- Never echo a secret or env value in logs, errors, test output or commit messages.
- Migrations are forward only and numbered from `0050`. Each one must apply cleanly on a database that already holds production-shaped rows; Task 3's index carries a pre-check query the deploy step runs first.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 655). Web: `pnpm test` (baseline 587), `pnpm exec tsc --noEmit`. Admin: `node --test tests/*.test.mjs` (baseline 112), `npx tsc --noEmit`, `npx next build`.

---

### Task 1: Approve makes the card public (API)

**Files:**
- Modify: `service/spotlight/storage.py` (`make_public`, `_bucket` if the client is needed)
- Modify: `tests/test_spotlight_storage.py` (replace the `_Object.put_object_acl` stub test)

- [ ] **Step 1: Write the failing test against the real client shape.** Build a real boto3 S3 client (`boto3.client('s3', region_name='us-east-1', aws_access_key_id='x', aws_secret_access_key='y')`), wrap it in `botocore.stub.Stubber`, expect exactly one `put_object_acl` call with `{'Bucket': <configured bucket>, 'Key': key, 'ACL': 'public-read'}`, monkeypatch `storage` so `make_public` uses that client, call `make_public(key)`, then `stubber.assert_no_pending_responses()`. Delete `test_make_public_sets_public_read_acl` and the `put_object_acl` method on the `_Object` stub.
- [ ] **Step 2: Run it, expect FAIL** (`AttributeError: 's3.Object' object has no attribute 'put_object_acl'` or an unmet Stubber expectation).
- [ ] **Step 3: Fix.** Call the client method the resource exposes:

```python
    bucket = _bucket()
    bucket.meta.client.put_object_acl(Bucket=bucket.name, Key=key, ACL='public-read')
```

The test injects its stubbed client through `bucket.meta.client`, so a small fake bucket with `name` and `meta.client` is the only hand-built object, and the method under test comes from botocore.
- [ ] **Step 4: Add the regression the run asked for.** Grep every other call on `_bucket()` and `Object(...)` in `service/spotlight/` and prove each method exists on boto3 1.35.99 with a one-line `hasattr` test over a real resource (`put_object`, `delete_objects`, anything else found). A method that does not exist is a second defect: fix it in this task and say so in the report.
- [ ] **Step 5: Full suite, commit** `fix(spotlight): approving a card makes its image public through the real s3 client`.

### Task 2: Consent binds to the revision on screen (API + web)

**Files:**
- Modify: `service/spotlight/revisions.py` (`approve_card`)
- Modify: `service/api/spotlight_card_routes.py` (`post_spotlight_card`)
- Modify: `ahavah-web/src/app/spotlight/card/[token]/page.tsx` (`handleApprove`, response type)
- Test: `tests/test_spotlight_card.py`, `tests/test_spotlight_revisions.py` (or where `approve_card` tests live), web page test

**Interfaces:**
- Produces: `POST /spotlight/card/<token>` approve body `{ decision: "approve", photo_uuid: string, revision: number }`. `revision` missing or not an integer answers 400. A `revision` that is not the current revision answers `200 { ok: true, result: "new_revision", revision: <current> }` and records nothing. Skip is unchanged and needs no revision.
- `approve_card(tx, request_key, person_id, photo_uuid, *, shown_revision: int, nonce=None)`.

- [ ] **Step 1: Failing API tests.** (a) Render revision 1, edit the caption so revision 2 is current and rendered, POST approve with `revision: 1`: result `new_revision`, no consent row on either revision, no new revision created, nonce not consumed. (b) The same POST with `revision: 2` records consent on revision 2. (c) Approve without `revision` answers 400. (d) Photo change with the current revision still returns `new_revision` and creates the new revision as today.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** In `approve_card`, after `rev = current_revision(...)` and before the photo branch:

```python
    if shown_revision != rev['revision']:
        # The member approved from a page showing an older card. Record
        # nothing; the page re-reads and asks about the card that is current.
        return 'new_revision'
```

In the route, parse `revision` with `isinstance(value, int) and not isinstance(value, bool)`, else `abort(400)`, and check the nonce is not consumed on this path (the stale answer must leave the token usable).
- [ ] **Step 4: Web.** `handleApprove` sends `revision` from the GET response it rendered (store it in state beside `photoUuid`; disable Approve while it is unknown, the same way a missing `photoUuid` disables it). The existing `new_revision` branch already re-reads and shows the ask-again notice; add a test that a stale POST shows the new card with the notice and does not show "Approved".
- [ ] **Step 5: Rendered check.** Re-run the acceptance probe's member C scenario (`ahavah-admin/tests/_evidence/acceptance-local-probe.mjs`, consent section) and paste the new `EXPECT ok` line into the report.
- [ ] **Step 6: Suites, one commit per repo** `fix(spotlight): member approval names the revision it was shown`.

### Task 3: Duplicate calls converge and answer cleanly (API)

**Files:**
- Create: `migrations/0050_spotlight_welcome_unique.sql`
- Modify: `service/api/admin/spotlight_routes.py` (`post_growth_spotlight_welcome`, `post_growth_spotlight_roundup`, the queue image route)
- Modify: the email send route in `service/api/admin/growth_routes.py`
- Test: `tests/test_growth_routes.py`, `tests/test_spotlight_routes.py` (whichever holds these routes' tests)

**Interfaces:**
- Produces: welcome duplicate answers `409` (unchanged code, now also under a race); roundup duplicate answers `200 { request_key, already: true }` under a race; email send duplicate answers `409 { error: "send_in_progress" }`; queue image upload losing a race answers `409 { error: "image_race" }`. The admin tick must treat 409 from the image upload as "someone else attached it", not as a render failure.

- [ ] **Step 1: Pre-check query** (goes in the migration header comment and the deploy step): 

```sql
SELECT subject_person_id, platform, count(*)
  FROM publishing_queue
 WHERE kind = 'welcome' AND status <> 'cancelled'
 GROUP BY 1, 2 HAVING count(*) > 1;
```

Zero rows is required before the index can be created. The migration itself runs the same check and raises a clear exception naming the conflict rather than letting `CREATE UNIQUE INDEX` fail opaquely.
- [ ] **Step 2: Migration.**

```sql
CREATE UNIQUE INDEX IF NOT EXISTS publishing_queue_one_live_welcome
    ON publishing_queue (subject_person_id, platform)
 WHERE kind = 'welcome' AND status <> 'cancelled';
```

This is the database form of the route's existing guard (non-cancelled welcome per member), per platform row.
- [ ] **Step 3: Failing race tests.** Eight threads behind a `Barrier` call the welcome route for one member: exactly one request key, exactly one E4 outbox row, every other answer 409, no 5xx. Eight threads call roundup in one ISO week: one request key, statuses all 200. Two threads submit the same email campaign id: one 200, one 409, no person queued twice. Two threads upload the same rendered image: one 200, one 409.
- [ ] **Step 4: Run, expect FAIL.**
- [ ] **Step 5: Implement.** Catch at the route, outside the `with api_tx()` block so the loser's transaction rolls back first (the same shape `post_spotlight_card` already uses):

```python
    try:
        with api_tx() as tx:
            ...
    except psycopg.errors.UniqueViolation:
        abort(409)
```

Roundup returns `dict(request_key=week_key, already=True)` from its `except`. Email send and image upload catch `psycopg.errors.SerializationFailure` and `UniqueViolation` and answer the 409 bodies above. Do not retry inside the request.
- [ ] **Step 6: Admin tick.** In `ahavah-admin/src/lib/tick.ts`, a 409 from `queueImage` counts as rendered by another run (no `render_failed`). Add a tick test. This step commits in the admin repo.
- [ ] **Step 7: Suites, commits** `fix(spotlight): duplicate welcomes, roundups, sends and uploads converge without errors` (API) and `fix(growth): a lost image race is not a render failure` (admin).

### Task 4: Old work is never starved (API + admin)

**Files:**
- Create: `migrations/0051_spotlight_render_backoff.sql`
- Modify: `service/api/admin/spotlight_routes.py` (`_Q_ROWS`, `_Q_REMOVALS`, a new render-failed route)
- Modify: `ahavah-admin/src/lib/tick.ts`, `ahavah-admin/src/lib/growth-api.ts`
- Test: API listing and route tests; admin tick test

**Interfaces:**
- Produces: `POST /admin/growth/queue/<request_key>/render-failed` (cron or admin session, same gate as the image route) body `{ reason: string }` (reason truncated to 200 chars, never contains a URL with a token). Increments `render_attempts` on every row of that request key and sets `render_next_attempt_at = NOW() + LEAST(interval '15 minutes' * 2 ^ render_attempts, interval '24 hours')`. Answers `{ request_key, render_attempts, render_next_attempt_at }`.
- Queue rows gain `render_attempts` and `render_next_attempt_at` (additive).

- [ ] **Step 1: Migration.** `ALTER TABLE publishing_queue ADD COLUMN IF NOT EXISTS render_attempts int NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS render_next_attempt_at timestamptz;` A successful image attach resets both (in `attach_platform_image`).
- [ ] **Step 2: Failing tests.** (a) One old unrendered row plus 205 newer rows whose render keeps failing: after each newer row has one render-failed call, the `needs_render` listing returns the old row first. (b) A row in backoff is not listed until its time passes. (c) 250 due removal tasks: the `pending=1` listing returns the 200 with the earliest `deadline_at`, oldest first. (d) The unfiltered Growth tab listing is unchanged (newest first).
- [ ] **Step 3: Implement.** `_Q_ROWS`: add `AND (%(needs_render)s::bool IS NOT TRUE OR q.render_next_attempt_at IS NULL OR q.render_next_attempt_at <= NOW())` and order `CASE WHEN %(needs_render)s::bool THEN q.created_at END ASC NULLS LAST, q.created_at DESC`. `_Q_REMOVALS`: order `CASE WHEN %(pending)s::bool THEN t.deadline_at END ASC NULLS LAST, t.created_at DESC`. Keep `LIMIT 200`.
- [ ] **Step 4: Admin.** The tick's render `catch` calls `growthApi.renderFailed(request_key, reason)`; a failure of that call is swallowed and counted in `render_failures` so one broken report never stops the loop.
- [ ] **Step 5: Re-run the 8c probe** and paste the `EXPECT` lines.
- [ ] **Step 6: Suites, commits** `fix(spotlight): renders back off and the oldest work is served first` (API, admin).

### Task 5: Graph calls fit the budget and follow current Meta docs (admin)

**Files:**
- Modify: `ahavah-admin/src/lib/publishing.ts`
- Modify: `ahavah-admin/src/app/api/growth/publish-due/route.ts` (default row count)
- Test: `ahavah-admin/tests/publishing.test.mjs` (or the existing worker test file)

- [ ] **Step 1: Failing tests** on the existing scaled-clock harness from the acceptance probe (8d): (a) Graph answers in 10 s and the container is ready on the 5th poll: the run ends within 60 virtual seconds, the row is released as not attempted, `media_publish` is never called. (b) Normal latency still publishes. (c) Once `media_publish` has been called, a late deadline does not abandon the row (the permalink lookup is skipped instead and the row is published without `post_url`). (d) The Facebook call sends `caption`, not `message`. (e) Token health calls `debug_token` with `access_token=<app id>|<app secret>` and reports `unknown` (not invalid) when either app variable is missing. (f) Without `META_GRAPH_VERSION`, calls go to `v26.0`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
  - A run deadline: `const deadline = startedAt + 45_000`; before `/media`, each poll, and `media_publish`, throw `new GraphError('Out of time for this run; it will retry.', { attempted: false })` when `Date.now() > deadline`. Before the permalink GET, skip when past the deadline.
  - Default claim `max` 1 in `publish-due` (the route may still pass a caller's explicit value).
  - Graph per-call timeout stays 15 s; state in a comment why 45 + 15 fits 60.
  - Page photos body `{ url, caption }`. When the response carries `post_id`, use it for `post_url` (`https://www.facebook.com/<post_id>`) and keep `external_post_id` as the value `DELETE` expects; verify which id `DELETE /{post-id}` takes against the Page photos reference cited in the research note and write the answer in a comment.
  - `debug_token`: read `AHAVAH_META_APP_ID` and `AHAVAH_META_APP_SECRET`; build the app token only in memory; never log it; missing either reports `unknown`.
  - `META_GRAPH_VERSION` default `v26.0`.
- [ ] **Step 4: Suites, build, commit** `fix(growth): publishing fits its time budget and follows current Graph docs`.

### Task 6: One JPEG card for every surface (admin + API)

The member approves the bytes they see; both platforms must publish those same bytes (acceptance 1a proved byte identity). Instagram accepts JPEG only, so the one rendered asset becomes JPEG for the preview and both platforms.

**Files:**
- Modify: `ahavah-admin/src/lib/spotlight-card.tsx` (encode to JPEG), `ahavah-admin/package.json` (add `sharp`), `ahavah-admin/src/lib/tick.ts` and `growth-api.ts` (field name)
- Modify: `service/spotlight/storage.py` (`validate_png` becomes `validate_card_image`, `put_png` becomes `put_card_image` with a content type), `service/spotlight/assets.py` (key extension), the queue image route in `spotlight_routes.py`
- Test: storage validation tests, image route tests, renderer test

**Interfaces:**
- Produces: the image route accepts `image_base64` plus `content_type` (`image/jpeg` or `image/png`) and still accepts the old `png_base64` field so the deployed admin keeps working between the API and admin deploys. Keys end `.jpg` or `.png` to match.

- [ ] **Step 1: Failing tests.** API: a valid JPEG of the card dimensions is accepted and stored with `ContentType: image/jpeg` and a `.jpg` key; a corrupt JPEG, wrong dimensions and oversize are refused with the existing three reasons; `png_base64` still works. Admin: `renderCard` returns bytes starting `FF D8 FF`, at the card dimensions, under the API's size cap, quality 90.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Admin: render with `ImageResponse` as today, then `sharp(png).jpeg({ quality: 90, chromaSubsampling: '4:4:4' }).toBuffer()` (4:4:4 keeps small caption text crisp). Confirm `sharp` loads on Vercel's Node runtime by running `npx next build` and checking the tick route's trace includes it. API: validate JPEG by magic bytes plus Pillow `Image.open(...).verify()` and size, the same way PNG is validated now.
- [ ] **Step 4: Rendered check.** Render the welcome and roundup frames to JPEG, view both at full size, and compare against the current PNG renders for text legibility. Attach both images to the report.
- [ ] **Step 5: Suites, build, commits** `feat(spotlight): cards render as jpeg so instagram accepts them` (admin) and `feat(spotlight): the image route stores jpeg cards` (API).

### Task 7: Runbook and environment templates (API + admin, docs and config only)

**Files:**
- Create: `docs/runbooks/spotlight-rollback-and-cron-pause.md`
- Modify: `.env.production.template` (API), `ahavah-admin/.env.example`
- Modify: `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md` (link the runbook)

- [ ] **Step 1: Runbook**, in this order, each step with the exact command or admin click and how to confirm it worked:
  1. Emergency stop, then `invites_enabled` off (Growth tab controls, or `POST /admin/growth/settings`).
  2. Stop the crons: remove or comment the three entries in `ahavah-admin/vercel.json` and push, or unset `CRON_SECRET` in Vercel so every cron call answers 401; say which is faster and which survives a redeploy.
  3. Roll back each deploy: API (droplet, previous image or `git checkout <sha>` and compose up with `--force-recreate`), web and admin (Vercel "Instant rollback" to the previous production deployment).
  4. What cannot be rolled back: forward-only migrations (all Spotlight migrations are additive, so the old code runs on the new schema; say so per migration 0046 to 0051), posts already live (delete through the Growth tab removal flow), mail already accepted by the provider.
  5. How to resume: the reverse order, and the checks before turning invites back on.
- [ ] **Step 2: Templates.** API `.env.production.template` gains `AHAVAH_GROWTH_CRON_SECRET=` with a comment that it must equal the admin project's value. Admin `.env.example` gains `AHAVAH_PHOTO_HOSTS=`, `AHAVAH_META_APP_ID=`, `AHAVAH_META_APP_SECRET=`, `META_GRAPH_VERSION=v26.0`, `CRON_SECRET=` and `AHAVAH_GROWTH_CRON_SECRET=`, each with a one-line comment. Blank values only.
- [ ] **Step 3: Commits** `docs(spotlight): rollback and cron-pause runbook` and `chore(growth): document every env variable the crons and publisher read`.

### Task 8: Review, acceptance re-run, deploy

- [ ] Whole-branch review across the three repos; one fix wave if needed.
- [ ] Re-run the acceptance probes for every sub-claim that FAILED (1a, 5a, 8a, 8c, 8d) and the 4d concurrent submit against the final heads; append a dated "Wave 3d re-run" section to the acceptance document with the new output. A sub-claim still failing blocks the deploy.
- [ ] On the owner's go: run Task 3's pre-check query on production, then deploy API (migrations 0050, 0051 apply), web, admin. Verify health, containers, cron container restart count 0, `spotlight_setting` flags unchanged, and the admin build lists the three crons.
- [ ] Update the memory note and handoff with the new heads.

## Self-review record

- Every FAILED sub-claim in the acceptance summary (1a, 5a, 8a welcomes, 8c, 8d) maps to a task; the non-failing noisy items (8a roundup 500s, 4d) are in Task 3; 10d is Task 7.
- Every Meta documentation finding maps to Task 5 or 6.
- Additivity: new response keys and columns only, except the approve body's required `revision`, which is unreachable in production while approvals are off, and the image route keeps its old field.
- Ordering: Task 3's admin step and Task 4's admin step both touch `tick.ts`; they run sequentially. Task 6 renames the storage helpers Task 1 touches, so Task 1 lands first.
- Owner decisions carried, not decided: the full resumable email job, moderation withdrawal, roundup participant consent, the missed-roundup button. The slow-Graph policy is ruled above and can be overturned.
