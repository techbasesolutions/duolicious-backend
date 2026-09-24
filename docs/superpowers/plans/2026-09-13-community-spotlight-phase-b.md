Linear: TEC-862

# Community Spotlight, Phase B (queue, worker, cards, member approval, member of the week) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish member-approved Spotlight cards to the Ahavah Facebook Page and Instagram from a reviewed queue, with member card approval (E4) and card-live (E5) emails, member of the week, opt-out cancellation and removal, click and sign-up attribution, and the Growth tab's queue, member of the week and controls panels.

**Architecture:** The President dashboard's publishing worker is ported into Ahavah's own stack: the queue table and claim function live in `duo_api` behind admin-gated API endpoints that also accept a shared cron secret; the per-minute worker and the daily tick are Vercel crons in `ahavah-admin` that call the Graph API directly; cards are rendered in `ahavah-admin` with `next/og` and the PNG bytes are posted to the API, which uploads them to the existing Spaces bucket. Eligibility is enforced in one API module and re-checked at claim. Member approval and opt-out are signed POST-only actions on `ahavah-web`.

**Tech Stack:** Flask + psycopg + pydantic (`ahavah-api`), pytest in the disposable Docker stack; Next.js 16.2.6 + React 19 (`ahavah-admin`, `ahavah-web`), `next/og` `ImageResponse` (ships inside `next`), `node:test` for the admin worker, vitest for web; Graph API v21.0; DigitalOcean Spaces via the existing `boto3` bucket client; SES.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` sections 3.2, 3.3, 3.4, 3.6, 3.7, 3.8 items 2, 3, 5, and 5, 6, 8, 9.

## Global Constraints

- Phase A must be merged first (branches `spotlight-phase-a`); this plan builds on `service/spotlight`, `service/campaigns`, `service/growth`, the Growth tab stats and emails panels, and the Claude Design SOT retrieved by `/handoff community spotlight` (card template, Growth tab queue and controls frames, E4 title image `title-card-ready.png`).
- Pushing `ahavah/main`, web `master` or admin `master` deploys production. Work on local branches `spotlight-phase-b`; hold every push for the owner's go.
- Never nest `api_tx` inside an open `api_tx` (the connection lock is not reentrant; Phase A hit a deadlock this way). Never run two API test processes at once.
- No em dashes in user-facing strings; sentence case; English only. No literal `%` inside SQL passed to psycopg. No link changes member state on GET. Tokens in `Authorization: Bearer` or a dedicated header, never in URLs. Names and photos on social only for opted-in members with an approved card. Every card render and every publish re-checks eligibility.
- Migration numbering: next free is `0041`; applied files are immutable.
- Backend tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline after Phase A: 295 plus the follow-ups). Admin tests: `node --test tests/*.test.mjs` (harness: `tests/auth-session.test.mjs` transpiles TS with `ts.transpileModule` and runs it in a `vm` sandbox stubbing `fetch`). Web: `pnpm test`, `tsc --noEmit`, eslint max-warnings 0.
- Pre-flight before Task 9 (worker goes live): the Meta app behind the Page token must hold `pages_manage_posts` and `instagram_content_publish` for Page `1100237303180442` and Instagram `17841447302854202` (owner action, spec 8.1); Vercel team techbase-hq is Pro, per-minute crons are supported (verified 2026-09-13).

---

### Task 1: Migration 0041 (publishing queue, removal tasks, attribution, claim function)

**Files:**
- Create: `migrations/0041_spotlight_queue.sql`
- Test: `tests/test_migration_0041.py`

**Interfaces:**
- Produces table `publishing_queue` (id uuid pk default gen_random_uuid(), request_key text not null, kind text check in (`welcome`,`roundup`,`member_of_week`,`highlight`), subject_person_id int null references person on delete set null, platform text check in (`facebook`,`instagram`), caption text not null default '', image_url text, image_key text, scheduled_for timestamptz, status text not null default 'review' check in (`awaiting_member`,`awaiting_render`,`review`,`scheduled`,`processing`,`published`,`failed`,`cancelled`), lease_until timestamptz, attempts int not null default 0, external_post_id text, error text, member_approved_at timestamptz, approved_photo_uuid uuid, created_by text, created_at, updated_at; unique (request_key, platform)); table `spotlight_removal_task` (id bigserial, queue_id uuid references publishing_queue on delete cascade, platform text, external_post_id text, reason text, done_at timestamptz, created_at); table `spotlight_setting` (key text pk, value text not null, updated_at) seeded with `scheduler_enabled=false`, `auto_welcome=false`, `auto_roundup=false`; column `campaign_click.signup_person_id int null references person on delete set null`; function `claim_spotlight_posts(max_rows int) returns setof publishing_queue` (`FOR UPDATE SKIP LOCKED`, sets `processing`, `lease_until = now() + interval '10 minutes'`, `attempts = attempts + 1`, selects `status='scheduled' AND scheduled_for <= now()`); index on `(status, scheduled_for)`.

- [ ] **Step 1: Failing test**

```python
# tests/test_migration_0041.py
import uuid
from database import api_tx

def _cols(tx, t):
    return {r['column_name'] for r in tx.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s", dict(t=t)).fetchall()}

def test_0041_schema():
    with api_tx('read committed') as tx:
        assert {'request_key','kind','subject_person_id','platform','caption','image_url','image_key','scheduled_for','status','lease_until','attempts','external_post_id','error','member_approved_at','approved_photo_uuid'} <= _cols(tx, 'publishing_queue')
        assert {'queue_id','platform','external_post_id','reason','done_at'} <= _cols(tx, 'spotlight_removal_task')
        assert {'key','value'} <= _cols(tx, 'spotlight_setting')
        assert 'signup_person_id' in _cols(tx, 'campaign_click')
        assert tx.execute("SELECT value FROM spotlight_setting WHERE key = 'scheduler_enabled'").fetchone()['value'] == 'false'

def test_0041_claim_skips_future_and_locks():
    rk = uuid.uuid4().hex
    with api_tx() as tx:
        tx.execute("INSERT INTO publishing_queue (request_key, kind, platform, status, scheduled_for) VALUES (%(rk)s, 'roundup', 'facebook', 'scheduled', NOW() - interval '1 minute'), (%(rk)s, 'roundup', 'instagram', 'scheduled', NOW() + interval '1 hour')", dict(rk=rk))
        rows = tx.execute("SELECT * FROM claim_spotlight_posts(5)").fetchall()
        mine = [r for r in rows if r['request_key'] == rk]
        assert len(mine) == 1 and mine[0]['platform'] == 'facebook' and mine[0]['status'] == 'processing' and mine[0]['attempts'] == 1
        again = tx.execute("SELECT * FROM claim_spotlight_posts(5)").fetchall()
        assert not [r for r in again if r['request_key'] == rk]
```

- [ ] **Step 2: Run, expect failure** (tables missing).

- [ ] **Step 3: Migration**

```sql
-- migrations/0041_spotlight_queue.sql
CREATE TABLE IF NOT EXISTS publishing_queue (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_key         text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('welcome','roundup','member_of_week','highlight')),
  subject_person_id   int REFERENCES person(id) ON DELETE SET NULL,
  platform            text NOT NULL CHECK (platform IN ('facebook','instagram')),
  caption             text NOT NULL DEFAULT '',
  image_url           text,
  image_key           text,
  scheduled_for       timestamptz,
  status              text NOT NULL DEFAULT 'review' CHECK (status IN ('awaiting_member','awaiting_render','review','scheduled','processing','published','failed','cancelled')),
  lease_until         timestamptz,
  attempts            int NOT NULL DEFAULT 0,
  external_post_id    text,
  error               text,
  member_approved_at  timestamptz,
  approved_photo_uuid uuid,
  created_by          text,
  created_at          timestamptz NOT NULL DEFAULT NOW(),
  updated_at          timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (request_key, platform)
);
CREATE INDEX IF NOT EXISTS publishing_queue_due_idx ON publishing_queue (status, scheduled_for);
CREATE INDEX IF NOT EXISTS publishing_queue_subject_idx ON publishing_queue (subject_person_id);

CREATE TABLE IF NOT EXISTS spotlight_removal_task (
  id               bigserial PRIMARY KEY,
  queue_id         uuid REFERENCES publishing_queue(id) ON DELETE CASCADE,
  platform         text NOT NULL,
  external_post_id text,
  reason           text NOT NULL,
  done_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spotlight_setting (
  key        text PRIMARY KEY,
  value      text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT NOW()
);
INSERT INTO spotlight_setting (key, value) VALUES ('scheduler_enabled','false'), ('auto_welcome','false'), ('auto_roundup','false')
ON CONFLICT (key) DO NOTHING;

ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS signup_person_id int REFERENCES person(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION claim_spotlight_posts(max_rows int)
RETURNS SETOF publishing_queue
LANGUAGE sql
AS $$
  UPDATE publishing_queue q
     SET status = 'processing',
         lease_until = NOW() + interval '10 minutes',
         attempts = attempts + 1,
         error = NULL,
         updated_at = NOW()
   WHERE q.id IN (
     SELECT id FROM publishing_queue
      WHERE status = 'scheduled' AND scheduled_for <= NOW()
      ORDER BY scheduled_for
      FOR UPDATE SKIP LOCKED
      LIMIT max_rows)
  RETURNING q.*;
$$;
```

`gen_random_uuid()` needs `pgcrypto` or Postgres 13+; check the stack (`SELECT gen_random_uuid()`), and add `CREATE EXTENSION IF NOT EXISTS pgcrypto;` at the top if it fails.

- [ ] **Step 4: Apply and run** (pipe the file through `docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d duo_api -v ON_ERROR_STOP=1`). Expected: PASS.

- [ ] **Step 5: Commit (hold)** `feat(db): spotlight publishing queue, removal tasks, settings, click attribution (0041)`

---

### Task 2: Eligibility and queue service (`service/spotlight/queue.py`)

**Files:**
- Create: `service/spotlight/eligibility.py`, `service/spotlight/queue.py`
- Modify: `service/spotlight/__init__.py` (opt-out hook)
- Test: `tests/test_spotlight_eligibility.py`, `tests/test_spotlight_queue.py`

**Interfaces:**
- `eligibility(tx, person_id: int) -> tuple[bool, str]`: reasons in order `not_opted_in`, `not_verified` (tier `none`), `under_18` (date_of_birth), `reported` (`skipped.reported = TRUE AND object_person_id = person_id`), `pending_deletion`, `not_activated`, `featured_recently` (`spotlight_last_featured_at > NOW() - interval '30 days'`), `no_photo` (no `photo` row with `moderation_status = 'approved'`), else `('ok', '')`. Pure SQL, one statement.
- `primary_photo_uuid(tx, person_id) -> str | None` (lowest `position` among approved photos).
- `photo_url(uuid: str, size: int | None = None) -> str` (`{USER_IMAGES_BASE_URL}/{size or 'original'}-{uuid}.jpg`).
- `create_candidate(tx, *, kind, subject_person_id, platforms=('facebook','instagram'), caption, created_by) -> str` returns `request_key`; inserts one row per platform with status `awaiting_member` for kinds with a subject, `awaiting_render` for `roundup`; refuses when eligibility is not ok (raises `ValueError(reason)`).
- `set_member_approval(tx, request_key, photo_uuid) -> int` moves `awaiting_member` rows to `awaiting_render`, stamps `member_approved_at`, `approved_photo_uuid` (must belong to the subject and be approved); rows older than 7 days in `awaiting_member` are cancelled by `expire_member_approvals(tx) -> int`.
- `attach_image(tx, request_key, image_key, image_url) -> int` moves `awaiting_render` to `review`.
- `set_status(tx, queue_id, status, *, external_post_id=None, error=None) -> None` with allowed transitions: review->scheduled (admin approve), review->cancelled, scheduled->cancelled, processing->published|failed|review, failed->scheduled (retry while attempts < 3).
- `cancel_for_member(tx, person_id, reason) -> int` cancels every non-published row and creates a `spotlight_removal_task` for each published Instagram row and returns the count; Facebook published rows get a removal task too, marked `reason='delete_via_api'` for the worker.
- `set_spotlight_opt_in` (Phase A) now calls `cancel_for_member(tx, person_id, 'opt_out')` when value is False, inside the same transaction.
- `settings(tx) -> dict[str,str]` and `set_setting(tx, key, value)`.

- [ ] **Step 1: Failing tests** (seed with `make_person`, then `UPDATE person SET spotlight_opt_in = TRUE, ahavah_verification_tier = 'bronze', date_of_birth = '1990-01-01'` and an approved `photo` row; assert each reason in order by toggling one condition at a time; assert `create_candidate` inserts two rows `awaiting_member`; `set_member_approval` with a foreign photo uuid raises; `cancel_for_member` cancels scheduled rows and creates a removal task for a published instagram row; `set_spotlight_opt_in(tx, pid, False)` cancels).

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement.** Eligibility as one SQL statement selecting the boolean conditions and choosing the first failing reason in Python. Queue operations as plain parameterised SQL. Photo ownership check: `SELECT 1 FROM photo WHERE uuid = %(u)s AND person_id = %(pid)s AND moderation_status = 'approved'`.

- [ ] **Step 4: Run tests, then the full suite.**

- [ ] **Step 5: Commit (hold)** `feat(spotlight): eligibility rules and publishing queue service`

---

### Task 3: Cron secret and queue endpoints (`service/api/admin/spotlight_routes.py`)

**Files:**
- Create: `service/api/admin/spotlight_routes.py`, `service/api/cron_auth.py`
- Modify: `service/api/__init__.py` (import), `service/config.py` (`GROWTH_CRON_SECRET = os.environ.get('AHAVAH_GROWTH_CRON_SECRET')`), `docker-compose.production.yml` and `docker-compose.test.yml` (env passthrough)
- Test: `tests/test_spotlight_routes.py`

**Interfaces:**
- `require_admin_or_cron(s) -> None`: passes when `require_admin` passes, or when the request carries header `X-Growth-Cron: <AHAVAH_GROWTH_CRON_SECRET>` and the secret is set (compare with `hmac.compare_digest`); otherwise 403. Endpoints used by the admin crons accept both; endpoints used only by humans stay `require_admin`.
- Admin-or-cron: `POST /admin/growth/queue/claim {"max": 2}` -> rows; `POST /admin/growth/queue/<id>/complete {"status": "published|failed|review", "external_post_id", "error"}`; `GET /admin/growth/queue?status=awaiting_render` -> rows with subject first name, age, country, approved photo URL; `POST /admin/growth/queue/<request_key>/image` (multipart or base64 PNG) -> API uploads to Spaces under `spotlight/<request_key>-<platform>.png` with `bucket.put_object(... ACL='public-read')` using the same client as `put_image_in_object_store`, returns `image_url`, calls `attach_image`; `GET /admin/growth/spotlight/candidates` -> members opted in within 14 days with no queue row yet (for welcomes) and the Monday roundup members; `GET /admin/growth/settings`.
- Admin only: `GET /admin/growth/queue` (all rows, with click and sign-up counts per row via `campaign_link.kind = 'post:<request_key>'`), `POST /admin/growth/queue/<id>/approve` (review->scheduled with `scheduled_for` body or the default next slot 12:00 or 18:00 UTC), `POST .../cancel`, `POST .../retry`, `POST .../reschedule`, `POST /admin/growth/queue/purge` (cancels every non-published row, returns count), `POST /admin/growth/settings {"key","value"}`, `GET /admin/growth/removals`, `POST /admin/growth/removals/<id>/done`, `POST /admin/growth/spotlight/member-of-week {"person_id","caption","scheduled_for"}` (creates the candidate and sends E4), `GET /admin/growth/spotlight/suggest` (eligible pool ordered by `spotlight_last_featured_at NULLS FIRST`, alternating gender against the last featured), `GET /admin/growth/token-health` (proxies Graph `debug_token` for `AHAVAH_META_PAGE_TOKEN`; the token itself lives in the admin app's Vercel env, so this endpoint receives `{"expires_at"}` from the admin worker's own health call and only stores it; keep it simple: the admin app reports token expiry to `POST /admin/growth/token-health` daily and the GET returns the stored value).
- Every human mutation writes `record_audit(tx, s, 'growth.queue.<action>', metadata=...)`.

- [ ] **Step 1: Failing tests** through the Flask client with the Phase A admin-session fixture pattern (`tests/test_growth_routes.py`): non-admin 403; cron header with wrong secret 403; correct secret claims and completes; approve sets `scheduled`; purge cancels; image endpoint with a 1x1 PNG stores the key and URL (monkeypatch the bucket client); settings round trip; audit rows written.

- [ ] **Step 2: Run, expect failure.** **Step 3: Implement.** **Step 4: Full suite.** **Step 5: Commit (hold)** `feat(spotlight): queue endpoints with admin or cron auth`

---

### Task 4: E4 card-ready and E5 card-live emails, member approval page

**Files:**
- Create: `emails/spotlight_card_ready.py`, `emails/spotlight_card_live.py`, `service/spotlight/approval.py` (signed card tokens: `make_card_token(request_key, email)`, `parse_card_token`, TTL 7 days, same HMAC scheme as `make_confirm_token`), `service/api/spotlight_card_routes.py` (`GET /spotlight/card/<token>` -> JSON `{first_name, age, country, photos:[{uuid,url}], expires_at, already}`; `POST /spotlight/card/<token> {"decision":"approve"|"skip","photo_uuid"}`)
- Create (web, from the SOT): `ahavah-web/src/app/spotlight/card/[token]/page.tsx` (preview, photo picker, two POST buttons; states: default, approved, skipped, expired, invalid)
- Test: `tests/test_spotlight_card.py`, `ahavah-web/tests/lib/spotlight-card.test.tsx`

**Interfaces:**
- `send_card_ready(email, request_key)` builds E4 (title `title-card-ready.png` pair, chip "Spotlight", body per spec 3.5 E4, button "Review my card" to `{WEB_BASE_URL}/spotlight/card/<token>`) and sends through `run_campaign` with `exempt=True`, `unsub_scope='notifications'`, campaign `e4`, campaign id `e4-<request_key>`.
- `send_card_live(email, request_key, post_url)` builds E5 (chip "Spotlight", title `title-spotlight.png` pair, the card image, button "Share your card" to the Page post URL, `exempt=True`, campaign `e5`).
- POST approve calls `set_member_approval`; skip calls `set_status(..., 'cancelled')` for the request key; both idempotent; GET never writes.

- [ ] Steps: failing tests (GET is read-only; POST approve moves rows to `awaiting_render` and refuses a photo not owned; expired token 410; E4 HTML escapes names; E5 includes the post URL), implement, web page transcribed from the SOT with a vitest test mirroring Phase A's confirm page test, gates, commit (hold) `feat(spotlight): card approval flow and E4/E5 emails`.

---

### Task 5: Card renderer in the admin app (`next/og`)

**Files:**
- Create: `ahavah-admin/src/lib/spotlight-card.tsx` (JSX transcription of the SOT card template: `PhotoCard`, `MemberOfWeekCard`, `RoundupCard`, `RoundupFallbackCard`), `ahavah-admin/src/app/api/growth/render/route.ts`, `ahavah-admin/assets/fonts/Ultra-Regular.ttf`, `PlusJakartaSans-Regular.ttf`, `PlusJakartaSans-Bold.ttf`, `NotoSansHebrew-Regular.ttf` (fonts loaded with `fs.readFile` at module init; Ultra from `Claude Design/uploads/Ultra/Ultra-Regular.ttf`, the others from Google Fonts release files, licences copied alongside)
- Test: `ahavah-admin/tests/spotlight-card.test.mjs` (renders each variant through `ImageResponse` to a PNG buffer, asserts 1080x1080 from the IHDR bytes and a non-trivial size; a name with Hebrew letters and one with Yoruba diacritics render without throwing), plus a pixel comparison script `scripts/compare-card.mjs` against the SOT frames exported at 1080 (manual gate, documented in the report)

**Interfaces:**
- `renderCard(input: { variant: 'photo'|'member_of_week'|'roundup'|'roundup_fallback'; firstName?: string; age?: number; country?: string; caption: string; photoUrl?: string; tiles?: Array<{firstName: string; photoUrl: string}>; count?: number; countries?: number }): Promise<Buffer>`.
- Route `POST /api/growth/render` (requires either the admin session cookie the app uses, or header `X-Growth-Cron` equal to `CRON_SECRET`) returning `image/png`.
- Photos are fetched by URL from `user-images.ahavah.app` only (host allowlist), embedded as data URIs for satori.

- [ ] Steps: failing node test, implement from the SOT, run the comparison script and attach the diff images to the report, commit (hold) `feat(admin): spotlight card renderer`.

Note (Wave 1, 2026-09-14): rendering now precedes member approval rather than following it. E4's link goes to a preview of the already-rendered current revision (`GET /admin/growth/queue` and the card route both expose `image_url` off `current_revision_id`), and member approval stays disabled (`spotlight_setting.approvals_enabled = 'false'`) until this task's renderer lands and the preview it produces is real. Bringing `approvals_enabled` to `'true'` is the activation step for this task, not a separate flag to add later.

---

### Task 6: Daily tick in the admin app (`/api/growth/tick`)

**Files:**
- Create: `ahavah-admin/src/app/api/growth/tick/route.ts`, `ahavah-admin/src/lib/growth-server.ts` (API client for server routes using `AHAVAH_API_ORIGIN` and header `X-Growth-Cron`), `ahavah-admin/vercel.json` (crons: `/api/growth/tick` `0 6 * * *`, `/api/growth/publish-due` `* * * * *`, `/api/growth/token-health` `0 7 * * *`)
- Test: `ahavah-admin/tests/growth-tick.test.mjs` (mocked fetch: candidates -> E4 sends requested through the API; `awaiting_render` rows -> render called once per row and image posted; on Monday the roundup is created; expired approvals cancelled; a render failure leaves the row and continues)

**Interfaces:**
- Tick sequence: `GET /admin/growth/candidates` -> for each welcome candidate `POST /admin/growth/spotlight/welcome {person_id}` (API creates the candidate and sends E4); `GET /admin/growth/queue?status=awaiting_render` -> render -> `POST .../image`; on Mondays `POST /admin/growth/spotlight/roundup` (API builds the roundup from approved newcomers, returns a request key, `awaiting_render`) -> render -> image; `POST /admin/growth/queue/expire-approvals`.
- Auth: Vercel cron calls carry `Authorization: Bearer <CRON_SECRET>`; the route checks it before anything else.

- [ ] Steps: tests, implement, commit (hold) `feat(admin): spotlight daily tick`.

---

### Task 7: Per-minute publisher (port of `server/publishing.js`)

**Files:**
- Create: `ahavah-admin/src/lib/publishing.ts` (port: `graph(path, token, body)`, `publishDue()`, Facebook `POST {AHAVAH_FB_PAGE_ID}/photos {url, message}`, Instagram `POST {AHAVAH_IG_USER_ID}/media` then poll `?fields=status_code` up to 5 x 2 s then `media_publish`; on a thrown error after a Graph call was attempted -> `complete(status='review')` with the fixed message; otherwise `failed`), `ahavah-admin/src/app/api/growth/publish-due/route.ts` (`maxDuration = 60`, bearer `CRON_SECRET`), `ahavah-admin/src/app/api/growth/token-health/route.ts`
- Test: `ahavah-admin/tests/publishing.test.mjs` (port of the President tests: idempotent claim, lost confirmation -> review and never retried across two runs with `graphCalls === 1`, Instagram poll, scheduler disabled makes no Graph call, API unreachable exits cleanly, eligibility re-check failure cancels the row without a Graph call, dry run `?dry=1` makes no Graph call)

**Interfaces:**
- `publishDue({ dry }: { dry: boolean }) -> { claimed, published, failed, review }`; reads `scheduler_enabled` from `GET /admin/growth/settings` first and returns immediately when false; each claimed row is re-checked with `GET /admin/growth/queue/<id>/eligible` before any Graph call; E5 is triggered by the API on `complete(published)` for rows with a subject.
- Env in Vercel: `AHAVAH_META_PAGE_TOKEN`, `AHAVAH_FB_PAGE_ID=1100237303180442`, `AHAVAH_IG_USER_ID=17841447302854202`, `META_GRAPH_VERSION=v21.0`, `CRON_SECRET`, `AHAVAH_GROWTH_CRON_SECRET`, `AHAVAH_API_ORIGIN=https://api.ahavah.app`.

- [ ] Steps: tests, implement, commit (hold) `feat(admin): spotlight publisher worker`.

---

### Task 8: Removal worker and Facebook delete

**Files:**
- Modify: `ahavah-admin/src/lib/publishing.ts` (`processRemovals()`: `GET /admin/growth/removals?pending=1`; for `reason='delete_via_api'` on Facebook call `DELETE /{external_post_id}` then `POST .../done`; Instagram tasks stay for a human), `service/spotlight/queue.py` (`cancel_for_member` also deletes the Spaces objects for the member's rows through a `delete_images(keys)` helper in the API; a nightly API cron `service/cron/spotlightretention` deletes objects for rows published more than 90 days ago)
- Test: admin `publishing.test.mjs` removal case; API `tests/test_spotlight_retention.py`

- [ ] Steps: tests, implement, register the cron in `service/cron/__init__.py` `gather(...)`, commit (hold) `feat(spotlight): removals and retention`.

---

### Task 9: Attribution (`spotlight_ref`) and per-post stats

**Files:**
- Modify: `ahavah-web/src/app/s/[key]/route.ts` (set cookie `ahavah.spotlight_ref=<key>`, 7 days, SameSite Lax, before the 307), `ahavah-web/src/app/onboarding/complete/page.tsx` or the finish-onboarding client call (send `spotlight_ref` from the cookie), `duotypes` `FinishOnboarding` (optional `spotlight_ref`), `service/person/__init__.py` finish-onboarding (stamp `person.spotlight_ref` and `UPDATE campaign_click SET signup_person_id = ... WHERE link_key = ref AND signup_person_id IS NULL` for the most recent non-bot click), `service/growth/queries.py` (`post_stats(tx, request_key) -> {clicks, signups}` excluding `ua_class = 'bot'`)
- Test: API `tests/test_spotlight_attribution.py`; web `tests/app/s-route.test.ts` (cookie set)

- [ ] Steps: tests, implement, commit (hold) `feat(spotlight): click and sign-up attribution`.

---

### Task 10: Growth tab queue, member of the week and controls panels (admin UI)

**Files:**
- Create: `ahavah-admin/src/components/admin/growth-queue-panel.tsx`, `growth-motw-panel.tsx`, `growth-controls-panel.tsx`, `growth-removals-list.tsx`
- Modify: `ahavah-admin/src/components/admin/tab-growth.tsx`, `src/lib/growth-api.ts` (queue, approve, cancel, retry, reschedule, purge, settings, suggest, memberOfWeek, removals, tokenHealth)
- Test: `ahavah-admin/tests/growth-api.test.mjs` (extended), Playwright at 1440 and 390 with fixture responses against the SOT frames

- [ ] Steps: `/sot-sync` from the Growth Tab SOT for the three remaining sections; wire; confirmation dialogs name counts and campaign ids; mobile read-only; commit (hold) `feat(admin): Growth tab queue, member of the week and controls`.

Note (Wave 1, 2026-09-14): the controls panel no longer has one scheduler switch and two per-kind auto flags. It shows three named controls instead: `invites_enabled` (pauses welcome and roundup candidate creation and the E4 invite), `publication_enabled` (pauses the per-minute claim only), and `external_access_enabled` (the emergency stop: halts every outbound Meta call, publishing and removals alike, and is the only control removals answer to). `auto_welcome` and `auto_roundup` are gone from settings; there is no auto-post toggle to wire until a revision-bound auto mode exists. The panel also reads `approvals_enabled` and `roundup_tiles_enabled` (both read-only status chips here; they flip on when Task 5's renderer and the per-member roundup approval flow respectively land, not from this panel).

---

### Task 11: Weekly email spotlight block and roundup names

**Files:**
- Modify: `emails/send_community_weekly.py` (`_week_context` sets `spotlight` from the most recent `published` `member_of_week` row of the last 7 days: first name, age, country, `image_url`, Facebook post URL `https://www.facebook.com/<external_post_id>`), `emails/community_weekly.py` unchanged
- Test: `tests/test_community_weekly.py` (spotlight block appears when a published row exists)

- [ ] Steps: test, implement, commit (hold) `feat(emails): weekly email carries the member of the week`.

---

### Task 12: Deploy Phase B and first real post

- [ ] Owner pre-flight: Meta app permissions granted for the Page and Instagram account; Page token minted and set in the admin Vercel env with the other variables in Task 7; `AHAVAH_GROWTH_CRON_SECRET` set on the droplet and in Vercel; `scheduler_enabled` stays `false`.
- [ ] Push order on owner go: API (0041 through the ledger runner), web, admin. Post-deploy: `GET /api/growth/token-health` reports days remaining; a `?dry=1` tick and publish-due run make no Graph call and return counts.
- [ ] First member of the week: owner picks in the Growth tab; the member approves via E4; admin approves; `scheduler_enabled` set to `true`; one real post; E5 received; owner shares the Page post into the group by hand.
- [ ] Auto flags after two clean weeks. Record everything in `docs/superpowers/plans/2026-09-13-spotlight-preflight.md`.

## Self-review record

- Spec coverage: 3.2 eligibility (Task 2), 3.3 kinds (Tasks 2, 6, 10), 3.4 growth loop (E5 in Task 4, attribution in Task 9, posting times default in Task 3), 3.6 engine (Tasks 1, 3, 7, kill switch and purge in Tasks 3 and 10), 3.7 rendering (Task 5), 3.8 panels 2, 3, 5 (Task 10), section 5 error handling (Tasks 2, 7), section 6 tests (each task), removal and retention (Task 8), weekly spotlight block (Task 11), rollout (Task 12).
- Deviations from the spec text: the spec numbered the queue migration 0039; it is 0041. Token health is reported by the admin worker to the API rather than proxied from the API, because the Page token lives only in Vercel.
- Type consistency: `request_key` is text (hex uuid) everywhere; statuses use the 0041 check list everywhere; the admin server client uses header `X-Growth-Cron` for the API and Vercel's `Authorization: Bearer CRON_SECRET` for its own routes.
