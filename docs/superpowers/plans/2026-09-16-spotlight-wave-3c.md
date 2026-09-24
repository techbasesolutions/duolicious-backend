Linear: TEC-942

# Community Spotlight, Wave 3c (fraud gate, cleanup, local acceptance run) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last known fraud gap before any live post, clear the deferred cleanup that is cheap and real, prepare the dated removal of the attribution compatibility branch, and produce an honest local run of the release acceptance matrix.

**Architecture:** Small, independent edits in the API and web repos, each on a `spotlight-wave-3c` branch, reviewed and deployed together. The legacy-branch removal is prepared on its own branch and held until its date. The acceptance run executes against the final branch heads on the disposable local stack and writes an evidence document; it reports failures, it does not fix them.

**Tech Stack:** Flask + psycopg (`ahavah-api`, pytest in the disposable Docker stack); Next.js 16 (`ahavah-web`, vitest, Playwright for screenshots); GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md`. Acceptance matrix: "Release acceptance matrix" in `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md`.

## Scope against the owner's list (2026-09-16)

The owner approved items 1 to 5 of the "what else can you do" list.

1. Fraud gate: an empty User-Agent is classified `unknown`, not `bot`, so a script that omits the header still mints a receipt. Task 1.
2. Local acceptance run. Task 4.
3. Legacy attribution branch removal date. The web deploy that began sending receipts landed on 2026-09-15, and the click cookie lives 7 days, so the branch may be deleted on or after **2026-09-22**. Task 1 writes the date; Task 3 prepares the removal on a held branch.
4. Photo-check cron dry run. **Resolved with no change.** `check_photos_forever()` is commented out in `service/cron/__init__.py:55`, so the loop never runs in any environment and its dry-run default is moot. When live it deletes every object-store image whose uuid has no `photo` or `onboardee_photo` row, on a 1 second poll; leaving it disabled is correct. Recorded here so nobody re-enables it casually.
5. Deferred cleanup. Tasks 1 and 2. One item on the owner's list is already done and is dropped: the "1 rows" and "1 members" copy was fixed in the Wave 3 fix wave (`growth-dialogs.tsx` pluralises, and the purge copy carries no bare count).

Not in scope: the growth cron secret (owner has not said yes), the photo-picker brief, the per-platform surface in the Growth tab, workspace housekeeping.

## Global Constraints

- Pushing `ahavah/main` or web `master` deploys production. Work on `spotlight-wave-3c` branches; the legacy removal lives on `spotlight-legacy-ref-removal` and is not merged before 2026-09-22.
- Never nest `api_tx`. Fixtures before transactions. No literal `%` in psycopg SQL. This suite really commits: never hardcode a key or receipt in a test.
- No em dashes (U+2014) on added lines; sentence case; **no attribution trailers in any commit**.
- Every response shape stays additive: the admin Growth tab is deployed separately.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 649). Web: `pnpm test` (baseline 586), `pnpm exec tsc --noEmit`, eslint on touched files.

---

### Task 1: API fraud gate and cleanup batch

**Files:** `service/campaigns/__init__.py`, `service/spotlight/queue.py`, `service/spotlight/revisions.py`, `service/spotlight/cleanup.py`, `service/api/admin/spotlight_routes.py`, `service/spotlight/attribution.py`, `.github/workflows/deploy-ahavah.yml`, `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md`, tests.

- [ ] **Empty User-Agent is a bot.** `_ua_class('')` and a whitespace-only agent return `'bot'`. Consequence to accept and document in the docstring: a click with no agent is still recorded but mints no receipt and is excluded from `post_stats` counts. Tests: empty and whitespace agents classify as bot; `record_click` with an empty agent returns no receipt; a real browser agent still mints one.
- [ ] **One `PLATFORMS` tuple.** `service/spotlight/queue.py` and `service/campaigns/__init__.py` each define `PLATFORMS = ('facebook', 'instagram')`. Keep the definition in `service/campaigns` and import it in `queue.py`. Check for an import cycle before choosing the direction; if one exists, move the constant to a module both can import and say so.
- [ ] **`edit_caption` picks the caption link, never the E5 share link.** Its lookup is `SELECT key FROM campaign_link WHERE kind = 'post:<rk>' LIMIT 1` with no ordering, and E5 mints a second link of the same kind. Order by `created_at ASC` so the caption link (minted at candidate creation, always first) wins. Test: create a candidate, mint a later same-kind link, edit the caption, assert the caption still carries the original key.
- [ ] **Abandoned list says when it is truncated.** `GET /admin/growth/removals` returns `abandoned` capped at 50 beside an uncapped `abandoned_cleanup` count. Add `abandoned_truncated: bool`. Additive. Test with more rows than the cap.
- [ ] **Deploy check tests the restart count.** `.github/workflows/deploy-ahavah.yml` echoes the cron container's `RestartCount` and never tests it. Fail the deploy when it is greater than zero. Match the surrounding shell style.
- [ ] **The compatibility branch carries its date.** In `service/spotlight/attribution.py`, replace the removal pointer with the concrete rule and date: safe to delete on or after 2026-09-22 (web deploy 2026-09-15 plus the 7 day cookie lifetime). Update handoff section 14's compatibility window to the same date, stated as fact now that the deploy has happened.
- [ ] Full suite once, one commit `fix(spotlight): an agentless click is a bot, one platform list, caption edits keep their own link, truncation and restarts are visible`.

### Task 2: Web token-page state block

**Files:** `ahavah-web/src/app/spotlight/confirm/[token]/page.tsx`, `ahavah-web/src/app/spotlight/card/[token]/page.tsx`, a new shared component under `src/components/app/`, tests.

- [ ] The invalid, expired and error states are near-identical JSX in both pages (about 40 lines each). Extract one component that both render, taking the copy object, the icon, the tone and the action. **No visual change is allowed.** Prove it: screenshot every affected state on both pages at 390 before and after with the existing Playwright route-interception harness, and compare pixel for pixel; any difference is a defect, not a judgment call.
- [ ] Existing tests for both pages must pass unchanged; add one test that the shared component renders each state's heading and action.
- [ ] One commit `refactor(spotlight): one state block for both token pages`.

### Task 3: Held removal of the legacy attribution branch

**Files:** `service/spotlight/attribution.py`, `tests/test_spotlight_attribution.py`, handoff.

- [ ] Branch `spotlight-legacy-ref-removal` from the Task 1 head. Delete the legacy campaign-key branch so a bare key returns False and stamps nothing. Change `test_attribute_does_not_overwrite` to use receipts (it currently rides the legacy branch) and invert `test_the_legacy_campaign_key_still_stamps_but_credits_nothing` into a test that a bare key now earns nothing. Update the `duotypes` docstring and handoff section 14 to say the window is closed.
- [ ] Full suite, one commit `fix(attribution): close the campaign-key compatibility window`. **Do not merge. Do not push.** It is held until 2026-09-22.

### Task 4: Local run of the release acceptance matrix

**Files:** Create `docs/superpowers/plans/2026-09-16-spotlight-acceptance-local.md`; probe scripts only under `tests/_evidence/`.

- [ ] For each of the eleven gates, state one of: **Proven locally** (name the tests or probe and paste the result), **Partial** (what is proven, what is not, and why), or **Needs staging or owner** (the exact reason). Run against the Task 1 and Task 2 branch heads on the disposable local stack.
- [ ] Reuse, do not re-derive: the Wave 1, 2, 3 and 3b evidence documents already map many rows to tests. Re-run those tests against the current heads rather than quoting old results, and cite the command and total.
- [ ] Where a gate asks for an integrated path no unit test covers (a real browser showing the final revision; opt-out during each queue state; two channels succeeding for one occurrence; duplicate cron invocations), write a probe under `tests/_evidence/` that drives it end to end locally and record its output.
- [ ] Known open items must be reported as open, not argued away: queue pagination beyond 200 rows (the Runtime gate's backlog starvation), Meta's PNG acceptance for the Instagram path, real account IDs and token scopes, and the first live exercise.
- [ ] No fixes in this task. A failing gate is a finding for the owner.
- [ ] One commit `docs(spotlight): local acceptance run against the release matrix`.

### Task 5: Review, deploy and schedule

- [ ] Whole-branch review of Tasks 1 and 2; one fix wave if needed.
- [ ] Deploy API then web. Verify health, containers, cron, flags unchanged.
- [ ] Record 2026-09-22 and the held branch name in memory so a later session merges it.

## Self-review record

- Owner items 1 to 5 each map to a task or an explicit resolution; the one already-done bullet is named and dropped.
- Additivity: the only response change is the new `abandoned_truncated` key.
- Ordering: Task 3 branches from Task 1; Task 4 runs after Tasks 1 and 2 land on their branches.
