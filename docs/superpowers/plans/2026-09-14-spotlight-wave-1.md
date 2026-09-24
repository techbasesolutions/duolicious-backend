Linear: TEC-869

# Spotlight Wave 1 (consent and lifecycle invariants) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close findings F01 to F05, F11 and the control-model part of F12 from the 2026-09-14 adversarial review so that no Spotlight card can publish without revision-bound consent from every pictured member, withdrawal from any entry point leaves no untracked publication, delivery state survives withdrawal, one feature occurrence spans both channels, and confirm links cannot re-enable consent after withdrawal.

**Architecture:** Additive records in `duo_api`: `spotlight_revision` (immutable content snapshots), `spotlight_revision_consent` (per person per revision), `spotlight_occurrence` (cooldown unit), `spotlight_token_nonce` (single-use tokens with a consent epoch), and new columns on `publishing_queue` (`current_revision_id`, `lease_token`, `cancellation_requested_at`, `delivery_state`). One withdrawal operation is called from every lifecycle entry point. The final dispatch check fails closed. The admin worker carries a lease token and treats a timeout after a sent publish call as `delivery_unknown`. Member approval stays disabled (setting `approvals_enabled=false`) until the designed renderer exists, because approval must be of the rendered revision. Roundups are count-only (`roundup_tiles_enabled=false`).

**Tech Stack:** as Phase B. Branches `spotlight-wave-1` stacked on `spotlight-phase-b` in `ahavah-api` and `ahavah-admin`; web untouched in Wave 1.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` as amended by Task 10 of this plan; triage: `docs/superpowers/plans/2026-09-14-spotlight-adversarial-remediation-triage.md`; review source: `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md`.

## Global Constraints

- Nothing is deployed; base branches are unchanged and no spotlight branch exists on a remote (verified 2026-09-14). Hold every push. Migrations are new numbered files from `0044`.
- Never nest `api_tx` (production code or tests: build fixtures before opening a transaction). One API implementer at a time. No literal `%` in psycopg SQL. No em dashes anywhere in touched files. No Co-Authored-By trailer. GET never mutates. Secrets in headers only.
- Tests for each finding encode the desired behaviour (the review's probes inverted): a passing test must prove the defect is gone, never that it exists.
- Backend command: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 383). Admin: `node --test tests/*.test.mjs` (baseline 35), `npx tsc --noEmit`, production build.
- Owner decisions in force: revisions now with approvals disabled until the renderer (1c); roundups count-only (2); campaign mail is at-least-once with visible uncertainty, addressed in Wave 2 (3); rework on branch (4); the review's acceptance matrix gates the first live post (5).

---

### Task 1: Migration 0044 (revisions, consent, occurrences, nonces, delivery state)

**Files:** `migrations/0044_spotlight_wave1.sql`; `tests/test_migration_0044.py`.

**Produces:**
- `spotlight_revision(id bigserial pk, request_key text not null, revision int not null, caption text not null, photo_uuid uuid, layout_version text not null default 'v1', channels text[] not null, participants jsonb not null default '[]', asset_hash text, image_key text, image_url text, created_by text, created_at timestamptz default now(), unique (request_key, revision))`.
- `spotlight_revision_consent(revision_id bigint references spotlight_revision on delete cascade, person_id int references person on delete cascade, role text check in ('subject','participant','admin'), approved_at timestamptz not null default now(), nonce text, primary key (revision_id, person_id, role))`.
- `spotlight_occurrence(id bigserial pk, kind text, person_id int references person on delete set null, request_key text not null, created_at timestamptz default now(), unique (request_key, person_id))`.
- `spotlight_token_nonce(nonce text pk, person_id int references person on delete cascade, purpose text check in ('confirm','card'), epoch int not null, issued_at timestamptz default now(), used_at timestamptz)`.
- `person.spotlight_consent_epoch int not null default 0`.
- `publishing_queue` columns: `current_revision_id bigint references spotlight_revision`, `lease_token text`, `cancellation_requested_at timestamptz`, `delivery_state text not null default 'none' check in ('none','attempting','published','delivery_unknown','failed')`, `post_url text`.
- `spotlight_removal_task` columns: `person_id int` (snapshot, no FK), `request_key text`.
- `spotlight_setting` seed rows: `approvals_enabled=false`, `roundup_tiles_enabled=false`, `invites_enabled=true`, `publication_enabled=false`, `external_access_enabled=true` (and `scheduler_enabled` retained as an alias read nowhere after Task 9).
- `claim_spotlight_posts(max_rows)` re-issued: additionally sets `lease_token = encode(gen_random_bytes(16),'hex')` and `delivery_state = 'attempting'`, and never selects rows with `cancellation_requested_at IS NOT NULL`.

Tests assert every column, constraint and seed, that claim sets a 32-hex lease token and `attempting`, and that a row with `cancellation_requested_at` set is not claimed.

---

### Task 2: Revisions and revision-bound approval (F01, F11 part)

**Files:** `service/spotlight/revisions.py`; modify `service/spotlight/queue.py` (`create_candidate` creates revision 1; `set_member_approval` replaced), `service/api/admin/spotlight_routes.py` (caption route and any photo change create a new revision; 409 `in_flight` when status in `scheduled`,`processing`), `service/spotlight/approval.py` (card tokens carry a nonce; approval requires `approvals_enabled='true'` and an `asset_hash` on the current revision, else 409 `preview_unavailable`); `tests/test_spotlight_revisions.py`.

**Produces:**
- `create_revision(tx, request_key, *, caption, photo_uuid, participants, channels, layout_version='v1', created_by) -> int` (next revision number; sets `publishing_queue.current_revision_id` for all rows of the key; clears approvals by construction since consent is per revision; if any row of the key is `scheduled` or `processing`, raises `ValueError('in_flight')`).
- `attach_render(tx, revision_id, asset_hash, image_key, image_url) -> None` (only when `asset_hash IS NULL`; raises `ValueError('already_rendered')`).
- `record_consent(tx, revision_id, person_id, role, nonce) -> None`.
- `consent_complete(tx, revision_id) -> bool` (subject rows: subject consent present; roundup: every participant in `participants` has consent, or `participants` is empty for count-only).
- `approve_card(tx, request_key, person_id, photo_uuid, nonce) -> str` (returns `'approved'|'already'`; raises `ValueError('approvals_disabled'|'preview_unavailable'|'photo_not_owned'|'nonce_used'|'epoch_stale')`; the chosen photo must equal `revision.photo_uuid`, otherwise a new revision is created with that photo and the caller re-renders before approval can proceed, returning `'new_revision'`).
- Card GET returns the current revision's `image_url`, `caption`, `revision` number and `preview_available` flag.

Tests: caption edit after approval creates revision 2 with no consent and approval is required again; edit of a scheduled row is 409; approve with approvals disabled is 409; approve without a rendered asset is 409; approve with a different photo yields a new revision; welcome consent does not satisfy a roundup revision (participants require their own rows).

---

### Task 3: Fail-closed dispatch check (F02)

**Files:** `service/spotlight/dispatch.py`; modify `service/api/admin/spotlight_routes.py` (`GET /queue/<id>/eligible` takes `lease_token` as a query parameter and delegates); modify `service/spotlight/eligibility.py` (`eligibility(tx, person_id, photo_uuid=None)`: when a photo uuid is given it must be owned, approved and present); `tests/test_spotlight_dispatch.py`.

**Produces:** `dispatch_check(tx, queue_id, lease_token) -> tuple[bool, str]` failing closed on: row missing; status not `processing`; lease token mismatch or `lease_until < now()`; `cancellation_requested_at` set; `current_revision_id` null; revision without `asset_hash`; `consent_complete` false; a non-roundup row with null subject; the subject not activated or pending deletion; the exact revision photo not owned/approved/present; for roundups every participant person passing `eligibility` with their tile photo; settings `publication_enabled` and `external_access_enabled` both `true`.

Tests: each condition individually flips the result to not ok with the named reason; deleting the chosen photo while another approved photo remains is not ok; hard-deleting the subject is not ok; a valid count-only roundup with empty participants and a rendered asset is ok.

---

### Task 4: One withdrawal operation (F03)

**Files:** `service/spotlight/withdrawal.py`; modify `service/spotlight/__init__.py` (`set_spotlight_opt_in(False)` calls `withdraw_member`), `service/person/__init__.py` (`delete_or_ban_account` calls it inside its transaction before the person row is touched), `service/cron/pendingdeletion/__init__.py` (calls it before hard delete), `service/moderation/__init__.py` (a new `moderation_action` hook if one exists; else document), `emails/send_community_weekly.py` (`_Q_SPOTLIGHT` adds `p.activated AND p.deletion_requested_at IS NULL`), `service/spotlight/queue.py` (removal tasks snapshot `person_id` and `request_key`); `tests/test_spotlight_withdrawal.py`.

**Produces:** `withdraw_member(tx, person_id, reason) -> dict` returning counts: rows cancelled (`cancellation_requested_at` set, status `cancelled` for rows not `attempting`), rows left attempting (only `cancellation_requested_at` set), roundup revisions re-issued without the member (or count-only), removal tasks filed for `published` rows (subject or participant) with snapshots, consent epoch incremented, nonces invalidated (`used_at = now()` for unused nonces of that person).

Tests: run every entry point (opt-out, `DELETE /account` through the Flask client, admin delete, the cron's function, ban) against rows in `awaiting_member`, `review`, `scheduled`, `processing`, `published`, single-member and roundup; assert cancellation, durable tasks that survive `DELETE FROM person`, exclusion from `_week_context()['spotlight']`, and that a person row deletion leaves the removal task readable.

---

### Task 5: Delivery state and late receipts (F04)

**Files:** modify `service/spotlight/queue.py` (`set_status` replaced by `record_receipt`), `service/api/admin/spotlight_routes.py` (`POST /queue/<id>/complete` requires `lease_token`), `ahavah-admin/src/lib/publishing.ts` and `growth-server.ts` (send `lease_token`; a timeout or network error after a sent publish call reports `delivery_unknown` with the request id when available; receipt-persistence failures are counted as `receipt_failed`, never as published), `tests/test_spotlight_delivery.py`, `ahavah-admin/tests/publishing.test.mjs`.

**Produces:**
- `record_receipt(tx, queue_id, lease_token, outcome, *, external_post_id=None, post_url=None, error=None) -> str` with outcomes `published|failed|delivery_unknown|review`; lease token must match the row's (409 `lease_mismatch` otherwise); `published` is accepted even when `cancellation_requested_at` is set: the row becomes `published` with `delivery_state='published'` and a removal task is filed immediately; a duplicate identical receipt is a no-op (200 `already`); `delivery_unknown` parks the row (`status='review'`, `delivery_state='delivery_unknown'`) and it is never auto-retried; `failed` keeps the retry path.
- Worker: `complete` always sends the lease token; on 409 `lease_mismatch` the row is left alone and counted `stale`; on receipt persistence failure after a confirmed external id the worker retries the completion call up to 3 times before counting `receipt_failed` and logging the external id in the result body (not the console).

Tests: processing -> opt-out -> published receipt with the lease token: row `published`, external id stored, removal task filed; wrong lease token 409; duplicate receipt idempotent; `delivery_unknown` never claimed again; admin tests for the timeout path and the retry of `complete`.

---

### Task 6: Feature occurrences and cooldown (F05), E5 once, Instagram permalink

**Files:** modify `service/spotlight/eligibility.py` (`featured_recently` reads `spotlight_occurrence` created within 30 days for a different `request_key`), `service/api/admin/spotlight_routes.py` (`complete(published)` creates the occurrence once per `(request_key, person)` for the subject or each participant and sends E5 only on the first channel confirmation), `service/spotlight/queue.py` (`stamp_featured` removed or made to write the occurrence), `ahavah-admin/src/lib/publishing.ts` (after an Instagram publish, `GET /{media_id}?fields=permalink` and send `post_url` in the receipt; Facebook `post_url` = `https://www.facebook.com/{post_id}`), `emails/spotlight_card_live.py` (uses `post_url` from the receipt); `tests/test_spotlight_occurrence.py`; admin tests.

Tests: Facebook then Instagram both complete for one request key; Instagram then Facebook likewise across two claim runs; a different request key for the same member within 30 days is blocked; roundup participants get one occurrence each; E5 sent once for two channels; Instagram receipt carries a permalink.

---

### Task 7: Single-use confirm and card tokens (F11)

**Files:** modify `service/spotlight/__init__.py` and `service/spotlight/approval.py` (tokens embed a nonce stored in `spotlight_token_nonce` with the person's current epoch; `parse_*` verifies signature, TTL, nonce unused, epoch equals current); `service/api/spotlight_routes.py`, `service/api/spotlight_card_routes.py` (POST marks the nonce used in the same transaction; a second identical POST returns the original result idempotently without changing state); `tests/test_spotlight_consent.py`, `tests/test_spotlight_card.py`.

Tests: confirm, withdraw, replay the same token: opt-in stays false; ordinary repeated confirmation returns `already` and stays true; a freshly issued token after withdrawal re-enables; GET remains read-only; card token replay after withdrawal is rejected.

---

### Task 8: Count-only roundups (decision 2)

**Files:** modify `service/spotlight/roundup.py` (tiles empty unless `roundup_tiles_enabled='true'`), `service/api/admin/spotlight_routes.py` (roundup creation stores an empty participant set; the eligible/dispatch path treats empty participants as count-only), admin `tick.ts` (fallback variant only when tiles empty; unchanged logic); tests.

---

### Task 9: Controls made honest (F12 control model)

**Files:** modify `service/api/admin/spotlight_routes.py` and `service/spotlight/queue.py` (claim requires `publication_enabled` and `external_access_enabled`; `invites_enabled` gates welcome/roundup candidate creation and E4; `external_access_enabled=false` makes `GET /queue/claim` and `GET /removals?pending=1` return empty with `halted: true`), admin `tick.ts` (reads `invites_enabled`), `publishing.ts` (`publishDue` reads `publication_enabled`; `processRemovals` reads `external_access_enabled` only, never the publication switch), `vercel.json` unchanged; `auto_welcome`/`auto_roundup` removed from settings and from the Growth tab plan (Task 10 of Phase B updated) until revision-bound auto mode exists; welcome cohort defined by `sign_up_time > now() - 14 days` (not opt-in time); roundup business key `roundup:<iso-year>-W<week>` stored as `request_key` so a duplicate tick converges; tests in both repos.

---

### Task 10: Spec and ledger amendments

**Files:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (3.1 revisions and nonces; 3.6 delivery states, lease tokens, late receipts, occurrence cooldown, three controls; 3.3 roundups count-only until per-member approval exists; 5 `delivery_unknown`; 6 the acceptance matrix; 7 auto flags removed), `docs/superpowers/plans/2026-09-13-community-spotlight-phase-b.md` (Task 5 preview-before-approval; Task 10 controls), both ledgers (record the reversed rulings and the resolving commits per finding), `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md` (append a Wave 1 section).

---

### Task 11: Wave 1 acceptance run

Run the review's acceptance rows for Consent, Lifecycle and Delivery against the local stack with the new tests, plus a dry `publishDue` and `runTick` against a stub API; record evidence in `docs/superpowers/plans/2026-09-14-spotlight-wave-1-evidence.md`. Wave 2 (storage immutability, mail outbox, cleanup jobs) follows as its own plan.

## Self-review record

- Coverage: F01 (Tasks 2, 8), F02 (3), F03 (4), F04 (5), F05 (6), F11 (7), F12 controls (9); F06 to F10 deferred to Wave 2 and 3 as the review sequences them; spec and ledger corrections (10); evidence (11).
- Consistency: `lease_token` set by the claim function (Task 1), required by `record_receipt` and `dispatch_check` (Tasks 3, 5) and sent by the worker (Task 5); `current_revision_id` set by `create_revision` (Task 2) and checked by `dispatch_check` (Task 3); occurrences written in Task 6 and read by eligibility; settings names identical across Tasks 1, 3, 9 and the admin worker.
- Outline level: as with Phase B, each task is expanded into a full brief (verbatim code and tests) before dispatch, arguing from this plan, the triage and the spec.
