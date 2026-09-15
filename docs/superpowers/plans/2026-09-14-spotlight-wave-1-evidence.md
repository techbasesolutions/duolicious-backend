# Community Spotlight, Wave 1: acceptance evidence

Date: 2026-09-15. API repo `D:/Antigravity/ahavah-api`, branch `spotlight-wave-1`, HEAD `7094181`. Admin repo `D:/Antigravity/ahavah-admin`, branch `spotlight-wave-1`, HEAD `85d6dd6`.

Source of the acceptance rows: `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md`, "Release acceptance matrix". Each matrix row's "Required proof" cell is a semicolon-separated list; the rows below split each clause out so a proof and verdict can be attached to it individually. Storage and Mail are Wave 2 and carry no evidence here.

## Test-suite runs

Each suite was run once.

### API

Command:

```
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q
```

Result: `491 passed, 9 warnings in 111.63s (0:01:51)`. The warnings are pre-existing Pydantic v2 deprecation notices in `duotypes/__init__.py` and `service/person/__init__.py` (`class-based config` and `.dict()`), unrelated to Spotlight.

### Admin

Command 1: `node --test tests/*.test.mjs`

Result: `tests 47`, `suites 0`, `pass 47`, `fail 0`, `cancelled 0`, `skipped 0`, `todo 0`, `duration_ms 2266.5588`.

Command 2: `npx tsc --noEmit`

Result: exit 0, no output.

Command 3: `npx next build`

Result: `Compiled successfully in 6.0s`; `Finished TypeScript in 5.5s`; `Generating static pages using 5 workers (6/6)`; exit 0. One pre-existing Next.js warning about an inferred workspace root (multiple lockfiles under `D:\Antigravity`), unrelated to Spotlight.

## Consent

Matrix row: "Browser shows final revision; approve/skip are POST; edits revoke approval; roundup needs each participant; chosen-photo deletion blocks dispatch"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Browser shows final revision | `tests/test_spotlight_card.py::test_get_is_read_only_and_post_approves`, `service/spotlight/approval.py::card_state` | Confirms the card GET route returns `image_url`, `caption` and `photo_uuid` read from the request's `current_revision_id`, so the JSON contract always names the current revision's rendered asset, not a stale one. | partial |
| approve/skip are POST | `tests/test_spotlight_card.py::test_get_is_read_only_and_post_approves`, `tests/test_spotlight_card.py::test_post_approve_is_gated_on_settings_and_render`, `tests/test_spotlight_card.py::test_post_skip_cancels_and_wrong_email_is_403`, `tests/test_spotlight_consent.py::test_confirm_get_is_read_only_and_reports_stale` | Confirms the card GET makes no state change and POST is required to approve or skip, gated on `approvals_enabled` and a rendered asset; a mismatched email is rejected with 403. | proven |
| edits revoke approval | `tests/test_spotlight_revisions.py::test_caption_edit_creates_new_revision_and_drops_consent`, `tests/test_spotlight_revisions.py::test_different_photo_makes_new_revision_without_consent`, `tests/test_spotlight_revisions.py::test_edit_of_in_flight_row_is_refused`, `tests/test_spotlight_revisions.py::test_create_revision_refuses_a_terminal_request` | Confirms a caption or photo change creates a new revision with no consent rows, an in-flight (scheduled/processing) row cannot be edited, and a published or cancelled row refuses edits outright with `terminal`. | proven |
| roundup needs each participant | `tests/test_spotlight_routes.py::test_roundup_route_with_tiles_needs_every_participant`, `tests/test_spotlight_routes.py::test_roundup_eligible_checks_every_tile_member`, `tests/test_spotlight_revisions.py::test_welcome_consent_does_not_satisfy_roundup`, `tests/test_spotlight_dispatch.py::test_each_condition_fails_closed[consent_incomplete]` | Confirms a tiled roundup revision records every pictured member as a participant, `consent_complete` is false until each one consents, a welcome approval cannot substitute for roundup consent, and dispatch fails closed with `consent_incomplete` when any participant has not consented. | proven |
| chosen-photo deletion blocks dispatch | `tests/test_spotlight_dispatch.py::test_deleting_chosen_photo_fails_even_with_another_approved_photo`, `tests/test_spotlight_dispatch.py::test_hard_deleted_subject_fails_closed` | Confirms `dispatch_check` fails closed (`not_rendered`/`subject_missing`, per the exact condition) when the revision's chosen photo is deleted even if another approved photo remains, and when the subject person row is hard-deleted. | proven |

Notes on `partial`: the "browser" itself, the Claude Design exact-card approval page, is Wave 3 work per the plan's implementation sequence; Wave 1 delivers only the API-side contract (`card_state`/`approval.py`) that such a page would read from. No browser page exists yet to visually confirm against a screenshot.

## Lifecycle

Matrix row: "Opt-out, deletion and moderation during every queue state leave no untracked publication; late receipts cause removal"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Opt-out, deletion and moderation during every queue state leave no untracked publication | `tests/test_spotlight_withdrawal.py::test_every_status_is_handled` (parametrized over `awaiting_member`, `review`, `scheduled`, `failed`, `processing`, `published`), `tests/test_spotlight_withdrawal.py::test_opt_out_withdraws`, `tests/test_spotlight_withdrawal.py::test_self_delete_route_withdraws`, `tests/test_spotlight_withdrawal.py::test_admin_ban_withdraws_before_delete`, `tests/test_spotlight_withdrawal.py::test_admin_deactivate_route_withdraws`, `tests/test_spotlight_withdrawal.py::test_admin_hard_delete_route_withdraws`, `tests/test_spotlight_withdrawal.py::test_pending_deletion_cron_withdraws_before_hard_delete`, `tests/test_spotlight_withdrawal.py::test_cron_second_pass_excludes_a_person_not_covered_by_the_withdrawal_pass`, `tests/test_spotlight_withdrawal.py::test_roundup_participant_reissued_without_member`, `tests/test_spotlight_withdrawal.py::test_reissue_leaves_a_published_sibling_row_untouched`, `tests/test_spotlight_withdrawal.py::test_removal_task_survives_person_delete`, `tests/test_spotlight_routes.py::test_roundup_eligible_checks_every_tile_member` | Confirms the single `withdraw_member` operation cancels non-attempting rows, marks processing rows for compensating removal without touching their attempt in place, and stamps published rows with removal tasks, for every queue status; confirms opt-out, self-delete, admin ban, admin deactivate, admin hard-delete, and the pending-deletion cron all call it before destructive deletion; confirms a roundup re-issue drops the withdrawing member without disturbing an already-published sibling row; confirms a reported (moderation-flagged) participant blocks dispatch of a not-yet-claimed card via the fail-closed eligibility check. | partial |
| late receipts cause removal | `tests/test_spotlight_delivery.py::test_late_receipt_after_withdrawal_is_recorded_and_removal_filed`, `tests/test_spotlight_delivery.py::test_late_receipt_on_a_roundup_files_one_unattributed_task`, `tests/test_spotlight_withdrawal.py::test_published_roundup_participant_files_removal_task` | Confirms a receipt that lands after withdrawal is still recorded (idempotent, lease-checked) and immediately files a removal task with the external post ID; confirms a roundup's late receipt files exactly one removal task rather than duplicating per participant; confirms a published roundup participant's own withdrawal files a removal task per platform. | proven |

Notes on `partial`: "moderation" is one of the six reasons `withdraw_member` accepts (`REASONS` in `service/spotlight/withdrawal.py`), but no production code path calls it with `reason='moderation'` today. Task 4's report (`.superpowers/sdd/2026-09-14-spotlight-wave-1/task-4-report.md`) searched the codebase for a moderation action that hides or bans a person server-side and found none (`service/moderation/__init__.py` is read-only); the only wired entry points are account deletion, ban, admin deactivate and admin hard-delete. Coverage today is: a reported member is excluded going forward by the fail-closed eligibility check (`test_roundup_eligible_checks_every_tile_member`), but there is no test of a moderation action that withdraws an *already scheduled or processing* card and files removal tasks for *already published* ones, because no such moderation hook exists to test. This is a finding for the controller, not a missing test.

## Delivery

Matrix row: "Two channels succeed for one occurrence; timeout/ack loss/worker death produce reconcilable attempts rather than duplicate posts"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Two channels succeed for one occurrence | `tests/test_spotlight_occurrence.py::test_sibling_channel_passes_after_first_publishes`, `tests/test_spotlight_occurrence.py::test_roundup_participants_each_get_an_occurrence`, `tests/publishing.test.mjs "facebook row publishes and completes with the lease token and post url"`, `tests/publishing.test.mjs "instagram row polls then publishes"`, `tests/tick.test.mjs "a roundup with tiles renders the roundup card once and uploads per platform"` | Confirms Facebook completing first does not block Instagram's eligibility for the same occurrence (F05); confirms each roundup participant gets their own occurrence row; confirms the admin worker completes both a Facebook and an Instagram queue row against the same lease token and records a post URL for each. | proven |
| timeout/ack loss/worker death produce reconcilable attempts rather than duplicate posts | `tests/test_spotlight_delivery.py::test_delivery_unknown_parks_and_is_never_reclaimed`, `tests/test_spotlight_delivery.py::test_wrong_or_missing_lease_rejected`, `tests/test_spotlight_delivery.py::test_duplicate_receipt_is_noop`, `tests/test_spotlight_delivery.py::test_set_status_cannot_leave_processing`, `tests/test_spotlight_routes.py::test_claim_reaps_expired_leases`, `tests/publishing.test.mjs "lost confirmation is reported as delivery_unknown, never as failed or published"`, `tests/publishing.test.mjs "a stale lease leaves the row alone and does not retry the receipt"`, `tests/publishing.test.mjs "a receipt that fails twice then succeeds is counted once as published"`, `tests/publishing.test.mjs "a receipt that never lands is reported with the external id in the body, not as published"` | Confirms a delivery whose confirmation never arrives parks in `review`/`delivery_unknown` with its lease cleared and is never re-claimed, rather than being retried or marked failed/published; confirms a receipt needs the exact lease token (rejecting a mismatch or a missing one) and a duplicate receipt for the same lease is a no-op; confirms an expired lease is reaped back to `review` by the claim route (models worker death); confirms the admin worker's own retried completion call is idempotent and an unconfirmed one is surfaced in the response body rather than silently counted as published. | proven |

## Controls

Matrix row: "Invite pause, publication pause and emergency stop behave as labeled; auto flags either work or are absent; cleanup deadlines visible"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Invite pause, publication pause and emergency stop behave as labeled | `tests/test_spotlight_controls.py::test_invites_gate_candidates_and_creation`, `tests/test_spotlight_controls.py::test_claim_reports_paused_and_halted`, `tests/test_spotlight_controls.py::test_removals_halted_by_emergency_stop_only`, `tests/test_spotlight_controls.py::test_expire_approvals_paused_while_approvals_disabled`, `tests/publishing.test.mjs "publish is paused when the api says paused"`, `tests/publishing.test.mjs "publish is halted by the emergency stop"`, `tests/publishing.test.mjs "removals run while publication is paused"`, `tests/publishing.test.mjs "removals stop under the emergency stop"`, `tests/publishing.test.mjs "token health honours the emergency stop"` | Confirms `invites_enabled=false` blocks new welcome/roundup candidate creation and empties the candidates list without touching publication; confirms `publication_enabled=false` reports `paused` and claims nothing while `external_access_enabled=false` reports `halted` independently; confirms cleanup (removals) keeps running while publication is paused but stops under the emergency stop; confirms the admin worker's publish, removals and token-health calls each read and honor the same three settings from the API rather than a client-local flag. | proven |
| auto flags either work or are absent | `tests/test_spotlight_controls.py::test_legacy_keys_gone_and_rejected` | Confirms `scheduler_enabled`, `auto_welcome` and `auto_roundup` are gone from `settings()` after migration 0045 and `set_setting` rejects any attempt to write them back with `bad_setting`. | proven |
| cleanup deadlines visible | none | No test exercises a visible deadline or escalation for overdue removal/cleanup work. | not covered |

Notes: "auto flags either work or are absent" resolves to "absent" by design (F12's remedy explicitly allows removing the controls and claims rather than implementing them); `service/spotlight/queue.py` documents that `auto_welcome`/`auto_roundup` were stored and never read, and migration 0045 deletes them. "Cleanup deadlines visible" is F09/Wave 2 territory (durable per-object cleanup jobs, overdue-work escalation); Wave 1 has no removal-task deadline or escalation surface to test against.

## Storage and Mail (Wave 2)

Per the plan's implementation sequence, F06 (immutable storage) and F09 (durable cleanup outbox) land in Wave 2, and F07/F08 (email outbox, delivery visibility) also land in Wave 2. No Wave 1 evidence is claimed for either matrix group.

## Dry runs

Harness pattern: the vm-sandbox / stub-fetch technique from `tests/publishing.test.mjs` and `tests/tick.test.mjs`, loading `src/lib/publishing.ts` and `src/lib/tick.ts` with a stub `fetch`. Script: `ahavah-admin/tests/_evidence/task11-dry-run-probe.mjs` (throwaway, not committed). Stub API: `paused: false`, `halted: false`, `invites_enabled: true`, one due row (`rk-evid-1`, `scheduled`, `scheduled_for` in the past) and one awaiting-render group (`rk-evid-2`, `awaiting_render`).

Command: `node tests/_evidence/task11-dry-run-probe.mjs`

`publishDue({ dry: true })` result, verbatim:

```json
{
  "claimed": 0,
  "published": 0,
  "failed": 0,
  "review": 0,
  "delivery_unknown": 0,
  "stale": 0,
  "receipt_failed": 0,
  "skipped": 1,
  "dry": true,
  "paused": false,
  "halted": false,
  "unrecorded": []
}
```

`runTick({ dry: true })` result, verbatim:

```json
{
  "welcomes_created": 0,
  "welcomes_skipped": 0,
  "roundup_created": false,
  "rendered": 1,
  "render_failed": 0,
  "approvals_expired": 0,
  "invites_paused": false,
  "approvals_paused": false,
  "dry": true
}
```

Calls made during the run (all GET, none to Graph, none a claim):

```
GET https://api.test/admin/growth/settings
GET https://api.test/admin/growth/queue?status=scheduled&due=1
GET https://api.test/admin/growth/candidates
GET https://api.test/admin/growth/queue?status=awaiting_render
```

Render calls recorded by the stub `renderCard`: 0 (the dry-run tick counts `rendered: 1` as an intent, matching the existing test `tests/tick.test.mjs "dry run performs no post and no render"`, but never invokes the renderer). Graph calls: 0. Claim calls (`/queue/claim`): 0.

## Open items for the controller

1. Welcome-during-pause gap (recorded per the brief regardless of findings): a welcome candidate created while `approvals_enabled` is false never receives its invite later; there is no send path once approvals reopen. Deferred to the Wave 2 mail outbox (E4 enqueued with the candidate, drained when approvals open). Operating rule until then: keep `invites_enabled` false whenever `approvals_enabled` is false. Proven by `tests/test_spotlight_controls.py::test_welcome_creates_candidate_without_invite_when_approvals_disabled`, which shows the candidate is created with `invite_sent=False` and nothing else picks it up.
2. Deploy-order gap (recorded per the brief regardless of findings): the admin worker must deploy after the API, because Task 9 changed the claim and removals responses from bare arrays to objects. An admin deploy ahead of the matching API deploy fails closed with 503 rather than misreading the old shape (`tests/publishing.test.mjs "api unreachable on claim surfaces as an error the route turns into 503"` models the same fail-closed path for an unreachable/incompatible API).
3. Consent, "Browser shows final revision" is `partial`: the API returns the current revision's `image_url`/`caption`, but the actual browser approval page is Wave 3 work and does not exist yet, so no screenshot-level proof is possible in Wave 1.
4. Lifecycle, "moderation" is `partial`: `withdraw_member` accepts a `moderation` reason but no production code path calls it; only a reported member's exclusion from future dispatch is covered (`test_roundup_eligible_checks_every_tile_member`). No moderation action exists today that would withdraw an in-flight card or file removal tasks for a published one under that reason. Task 4's report treats this as a closed investigation ("no hook exists, none warranted") rather than an outstanding defect, but the acceptance row as worded is not fully proven.
5. Controls, "cleanup deadlines visible" is `not covered`: no removal-task deadline or overdue-work escalation exists in Wave 1; this is F09/Wave 2 scope.
6. Storage and Mail matrix groups carry no Wave 1 evidence (F06/F07/F08/F09 are Wave 2 per the plan).
7. No test failures, flaky tests or orphan rows were observed in either suite run. Both suites, tsc and the admin build were clean on the first run.
8. Roundup branch of late removal-task filing (flagged in the ledger at Task 5's review) is now covered: `tests/test_spotlight_delivery.py::test_late_receipt_on_a_roundup_files_one_unattributed_task` exists and passes as part of the 491.
