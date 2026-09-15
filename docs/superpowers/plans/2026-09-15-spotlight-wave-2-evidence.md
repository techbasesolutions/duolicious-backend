# Community Spotlight, Wave 2: acceptance evidence

Date: 2026-09-15. API repo `D:/Antigravity/ahavah-api`, branch `spotlight-wave-2`, HEAD `d369dc4` (the docs commit `docs(spotlight): wave 2 amendments` sits on top and does not change test counts). Admin repo `D:/Antigravity/ahavah-admin`, branch `spotlight-wave-2`, HEAD `1b9479d`.

The review of the API halves of Tasks 6 and 7 (`d369dc4`) completed during this task with "needs fixes": two important findings (invite-pending has no terminal state for a permanently ineligible request; the strict readiness block on an unresolved sibling is an operator-gated dead end with no surface) plus six minors. Fix round 1 is ruled in the ledger and queued behind this evidence commit; it has not landed, so every row below is evidence against `d369dc4` as it stands today, not against the fixed version.

Source of the acceptance rows: `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md`, "Release acceptance matrix". Each matrix row's "Required proof" cell is a semicolon-separated list; the rows below split each clause out so a proof and verdict can be attached to it individually, matching the Wave 1 evidence document's format. Consent, Lifecycle, Delivery and Controls are re-run here because Wave 2 touched shared code paths (`storage.delete_images`'s signature, `withdrawal.py`, `complete_render_if_ready`); Storage and Mail are new this wave.

## Test-suite runs

Each suite was run once.

### API

Command:

```
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q
```

Result: `590 passed, 9 warnings in 23.17s`. The warnings are the same pre-existing Pydantic v2 deprecation notices as Wave 1, unrelated to Spotlight.

### Admin

Command 1: `node --test tests/*.test.mjs`

Result: `tests 59`, `suites 0`, `pass 59`, `fail 0`, `cancelled 0`, `skipped 0`, `todo 0`, `duration_ms 1665.3974`.

Command 2: `npx tsc --noEmit`

Result: exit 0, no output.

Command 3: `npx next build`

Result: `Compiled successfully in 3.8s`; `Finished TypeScript in 3.8s`; `Generating static pages using 5 workers (6/6)`; exit 0. The same pre-existing Next.js workspace-root warning as Wave 1 (multiple lockfiles under `D:\Antigravity`), unrelated to Spotlight.

## Storage (Wave 2)

Matrix row: "Immutable identity; concurrent edit safe; corrupt images rejected; failed deletion retried with retained keys"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Immutable identity | `tests/test_spotlight_assets.py::test_complete_render_if_ready_is_false_until_every_row_has_a_hash`, `tests/test_spotlight_routes.py::test_image_route_uses_content_hashed_key_and_private_acl` | Confirms the object key embeds the revision id and the sha256 of the uploaded bytes (`spotlight/<request_key>/<revision_id>-<sha256 prefix>-<platform>.png`), so identical bytes always resolve to the identical key and the row's `image_sha256` matches the uploaded data's own hash. | proven |
| Concurrent edit safe | `tests/test_spotlight_assets.py::test_attach_platform_image_refuses_once_the_revision_is_rendered`, `tests/test_spotlight_assets.py::test_complete_render_refuses_while_a_sibling_is_parked_with_unresolved_delivery`, `tests/test_spotlight_routes.py::test_image_route_superseded_when_revision_changes_mid_upload` | Confirms the compare-and-set attach refuses once the pinned revision already has a render attached, refuses while a sibling row's delivery is unresolved, and answers `superseded` (queuing the orphan for cleanup) when the revision changed mid-upload instead of overwriting an approved image. | proven |
| Corrupt images rejected | `tests/test_spotlight_storage.py::test_validate_png_rejects[...]` (not a PNG, wrong dimensions), `tests/test_spotlight_storage.py::test_validate_png_rejects_oversize`, `tests/test_spotlight_routes.py::test_image_route_rejects_invalid_png` | Confirms `validate_png` rejects non-PNG data, the wrong pixel dimensions and an oversized payload with a typed `InvalidImage` reason, and the image route answers 400 `invalid_image` before ever calling `put_png`. | proven |
| Failed deletion retried with retained keys | `tests/test_spotlight_cleanup.py::test_partial_confirmation_retains_unconfirmed_keys`, `tests/test_spotlight_cleanup.py::test_abandon_after_max_attempts`, `tests/test_spotlight_cleanup.py::test_a_key_referenced_again_after_enqueue_is_not_deleted`, `tests/test_spotlight_cleanup.py::test_the_row_that_owned_the_key_at_enqueue_does_not_block_its_own_cleanup`, `tests/test_spotlight_cleanup.py::test_a_done_job_does_not_block_a_new_one_for_the_same_key` | Confirms a key storage did not confirm stays on the queue row and its job backs off rather than being cleared; a job that exhausts ten attempts is abandoned with an alert line and stays in the table; a key that came back to life between enqueue and the batch is left alone rather than deleted; and a key's own retained reference (the normal retention/removal/withdrawal case) never blocks its own cleanup, while a prior, finished job for the same key never blocks its next lifetime. | proven |

## Mail (Wave 2)

Matrix row: "Concurrent submits serialize; process death surfaces as unknown, not lost or duplicated; acceptance uncertainty is visible; suppression checked at send; E4/E5 survive restart"

| Acceptance row (verbatim) | Proof | What the test does | Verdict |
| --- | --- | --- | --- |
| Concurrent submits serialize | `tests/test_email_outbox.py::test_concurrent_drains_never_double_send`, `tests/test_email_outbox.py::test_enqueue_is_the_idempotency_point` | Confirms two `reserve()` calls on separate connections never see the same row (`FOR UPDATE SKIP LOCKED`), and a retried `enqueue` for the same (campaign, campaign_id, person_id) adds nothing rather than a second row. | proven |
| Process death surfaces as unknown, not lost or duplicated | `tests/test_email_outbox.py::test_reservation_that_never_completes_becomes_acceptance_unknown`, `tests/test_email_outbox.py::test_a_drain_that_dies_mid_send_strands_exactly_one_row`, `tests/test_email_outbox.py::test_the_next_drain_picks_up_where_a_dead_one_stopped`, `tests/test_email_outbox.py::test_a_send_that_was_never_recorded_is_never_sent_again` | Confirms a reservation older than the timeout becomes `acceptance_unknown` with no SMTP call on the reaping drain; a drain that dies mid-send (an aborting stub) strands exactly the one row it was sending, not the whole batch; the next drain resumes with the rest; and a message whose acceptance write never landed is never re-sent once its reservation is reaped, closing the gap that would double mail a member. | proven |
| Acceptance uncertainty is visible | `tests/test_email_outbox.py::test_status_counts`, `tests/test_email_outbox.py::test_status_buckets_a_state_it_does_not_know` (admin surface: `GET /admin/growth/emails/<campaign>/status/<campaign_id>` per the plan, exercised indirectly through `outbox.status`) | Confirms `status()` reports per-state counts including `acceptance_unknown`, so a stranded reservation is a queryable state rather than a silent gap. | proven |
| Suppression checked at send | `tests/test_email_outbox.py::test_suppression_and_unsubscribe_checked_at_send_time`, `tests/test_email_outbox.py::test_an_address_suppressed_after_enqueue_is_never_sent`, `tests/test_email_outbox.py::test_the_frequency_cap_is_rechecked_at_send_time` | Confirms suppression, scope-unsubscribe and the frequency cap are re-checked at drain time, not only at enqueue: a member who unsubscribes or becomes suppressed between enqueue and drain is skipped, never sent. | proven |
| E4/E5 survive restart | `tests/test_spotlight_card.py::test_e4_is_enqueued_in_the_candidate_transaction_and_survives_restart` (E4); E5's enqueue-in-transaction path is exercised by the existing complete/reconcile route tests in `tests/test_spotlight_routes.py` and `tests/test_spotlight_delivery.py`, which were updated for the outbox in Task 4 and still pass at 590 | Confirms E4 is written to `email_outbox` inside the same transaction as the candidate it belongs to, with no thread started, and a later drain with a stub SMTP sends exactly one; E5's call sites moved from `send_card_live_async` (a daemon thread) to `enqueue_card_live` inside the completing transaction, covered by the same route suite that proved the underlying receipt handling in Wave 1. | proven |

## Consent (re-run)

Matrix row: "Browser shows final revision; approve/skip are POST; edits revoke approval; roundup needs each participant; chosen-photo deletion blocks dispatch"

All five Wave 1 proofs (`tests/test_spotlight_card.py`, `tests/test_spotlight_revisions.py`, `tests/test_spotlight_routes.py`, `tests/test_spotlight_dispatch.py`) are unchanged in name and still pass at `d369dc4` as part of the 590. Wave 2 touched `card_state`'s `image_url` (now a presigned link, `tests/test_spotlight_card.py::test_card_state_presigns_private_preview`) and the approve route (`tests/test_spotlight_routes.py::test_approve_makes_the_row_image_public_after_commit`, replacing Wave 1's simpler approve test with the storage-call version). Verdicts are unchanged from Wave 1: **proven** for "approve/skip are POST", "edits revoke approval", "roundup needs each participant" and "chosen-photo deletion blocks dispatch"; **partial** for "Browser shows final revision" (the Claude Design approval page itself is still Wave 3 work; only the API contract is proven).

## Lifecycle (re-run)

Matrix row: "Opt-out, deletion and moderation during every queue state leave no untracked publication; late receipts cause removal"

The Wave 1 proofs in `tests/test_spotlight_withdrawal.py` and `tests/test_spotlight_delivery.py` are unchanged in name and still pass at `d369dc4`. Wave 2 changed what `withdraw_member`'s cancelled rows do with their stored images (`enqueue_asset_delete` instead of an inline `delete_images` call), covered by `tests/test_spotlight_cleanup.py::test_withdrawal_enqueues_cancelled_keys_instead_of_deleting`, and added a 72-hour `deadline_at` to every newly filed removal task (`service/spotlight/withdrawal.py`), covered by `tests/test_spotlight_cleanup.py::test_overdue_removals_counted`. Verdicts are unchanged from Wave 1: **partial** for "Opt-out, deletion and moderation..." (the moderation-reason gap noted in the Wave 1 evidence document is still open, unchanged by Wave 2), **proven** for "late receipts cause removal".

## Delivery (re-run)

Matrix row: "Two channels succeed for one occurrence; timeout/ack loss/worker death produce reconcilable attempts rather than duplicate posts"

The Wave 1 proofs in `tests/test_spotlight_occurrence.py`, `tests/test_spotlight_delivery.py`, `tests/publishing.test.mjs` and `tests/tick.test.mjs` are unchanged in name and still pass. Wave 2 did not change the lease, claim or receipt mechanics; `complete_render_if_ready` (Task 5's residual fix) now excludes a sibling row parked with an unresolved delivery from the readiness check, the pin and the compare-and-set, covered by `tests/test_spotlight_assets.py::test_complete_render_refuses_while_a_sibling_is_parked_with_unresolved_delivery`. Both verdicts are unchanged from Wave 1: **proven**.

## Controls (re-run)

Matrix row: "Invite pause, publication pause and emergency stop behave as labeled; auto flags either work or are absent; cleanup deadlines visible"

| Acceptance row (verbatim) | Proof | What changed since Wave 1 | Verdict |
| --- | --- | --- | --- |
| Invite pause, publication pause and emergency stop behave as labeled | Wave 1's proofs, unchanged, plus `tests/test_spotlight_cleanup.py::test_emergency_stop_halts_cleanup_and_counts_outstanding` (already proven in Wave 1) and the dry run below | No change to the three controls themselves; the emergency stop now also gates the cleanup-job batch, which did not exist in Wave 1. | proven |
| auto flags either work or are absent | `tests/test_spotlight_controls.py::test_legacy_keys_gone_and_rejected` (unchanged) | No change. | proven |
| cleanup deadlines visible | `tests/test_spotlight_cleanup.py::test_overdue_removals_counted`, `tests/test_spotlight_routes.py::test_removal_failed_records_evidence_and_backs_off`, `tests/test_spotlight_routes.py::test_removal_failed_permission_needs_attention`, `tests/test_spotlight_routes.py::test_removal_failed_on_done_task_is_404`, admin `tests/publishing.test.mjs` "a removal whose target is gone is marked done only on the documented error pair", "a permission error reports needs_attention", "a network error reports the failure and moves on", "an investigate removal task is listed and never deleted" | Wave 1 had no removal-task deadline or escalation surface at all (verdict was "not covered"). Wave 2 adds `deadline_at`, `overdue_removals`, `outstanding_cleanup` on `GET /admin/growth/removals`, a `?attention=1` view, and `POST /admin/growth/removals/<id>/failed` recording evidence and pushing the deadline forward on backoff. | proven, was not covered in Wave 1 |

Note: Task 6's own review found the readiness block on an unresolved sibling to be an operator-gated dead end with no surface, and invite-pending has no terminal state for a permanently ineligible request; fix round 1 addressing both is ruled in the ledger but not yet landed (see the header). Neither finding touches the "cleanup deadlines visible" row's own required proof, but both are open defects against `d369dc4` and are carried into the open items below rather than hidden by the proven verdict.

## Dry runs

Harness: a throwaway script run inside the docker test stack (not committed; `tests/_evidence/` is scratch space), calling `service.campaigns.outbox.drain` and `service.spotlight.cleanup.run_cleanup_batch` directly with a stub SMTP object and a stub storage delete function, exactly as `tests/test_email_outbox.py` and `tests/test_spotlight_cleanup.py` do, but outside pytest so the results below are a live demonstration rather than an assertion. `cleanup.run_cleanup_batch` was scoped with `target_prefix` to the probe's own keys so it never touched unrelated jobs already pending in the shared test database. The script created and deleted its own fixture people, outbox rows and cleanup jobs; nothing it created was left behind (verified by a direct count query after the run).

Command: `python tests/_evidence/wave2-dry-run-probe.py` (run inside the api container via `docker compose -f docker-compose.test.yml run --rm ... --entrypoint bash api -c "PYTHONPATH=/app python tests/_evidence/wave2-dry-run-probe.py"`).

`outbox.drain`, verbatim:

```
enqueue -> 833
drain (accepts one) -> {"reserved": 1, "accepted": 1, "skipped": 0, "failed": 0, "unknown_reaped": 0}
smtp.sent -> [{"subject": "Wave 2 evidence probe", "body": "<p>hi</p>", "to_addr": "wave2-probe-<uuid>@ahavah-test.invalid", "from_addr": "hello@ahavah.app", "list_unsubscribe": null}]
drain (nothing due) -> {"reserved": 0, "accepted": 0, "skipped": 0, "failed": 0, "unknown_reaped": 0}
drain (reaps a lost reservation) -> {"reserved": 0, "accepted": 0, "skipped": 0, "failed": 0, "unknown_reaped": 1}
smtp3.sent (must be empty) -> []
lost row state -> acceptance_unknown
```

`cleanup.run_cleanup_batch`, verbatim:

```
run_cleanup_batch (partial confirmation) -> {"reserved": 2, "done": 1, "retried": 1, "abandoned": 0, "missing": 0, "halted": false}
cleanup_job rows -> [{"target": ".../ok.png", "state": "done", "attempts": 1}, {"target": ".../bad.png", "state": "pending", "attempts": 1}]
run_cleanup_batch (halted) -> {"reserved": 0, "done": 0, "retried": 0, "abandoned": 0, "missing": 0, "halted": true, "outstanding": 420}
delete calls made while halted -> []
```

The halted run's `outstanding: 420` is the total pending count across the whole (long-lived, disposable) test database, not scoped to the probe's own three jobs; `outstanding_jobs` counts table-wide by design, matching production behavior where the emergency stop reports every stalled job, not just one caller's own. This is pre-existing accumulation in the shared test stack from prior test runs (nothing in this wave's tests leaves jobs pending on a passing run; the number reflects however many disposable-stack sessions have run cleanup-touching tests without a full teardown) and is unrelated to this evidence run.

## Open items for the controller

1. Task 6's own review (completed during this task, after dispatch) found "needs fixes": invite-pending has no terminal state for a permanently ineligible request, so the tick would call it forever for a request that can never become eligible; and the strict readiness block added for an unresolved sibling delivery is an operator-gated dead end with no surface for an operator to act on. Fix round 1 is ruled in the ledger (cancel an ineligible request out of the backlog with an audited reason; surface `render_blocked` on the affected queue and removal rows; four further minors) and queued behind this commit. Until it lands, `d369dc4` carries both defects.
2. Consent, "Browser shows final revision" stays `partial`, unchanged from Wave 1: the actual browser approval page is still Wave 3 work.
3. Lifecycle, "moderation" stays `partial`, unchanged from Wave 1: no production code path calls `withdraw_member` with `reason='moderation'` today.
4. F10 (attribution) and the remainder of F12 (idempotent-tick work beyond the weekly key, any auto mode) remain deferred to Wave 3; the acceptance-matrix staging run remains Wave 4. The matrix's six rows now all have Wave 1 or Wave 2 evidence, but no row has been exercised end to end against a staging environment or real Meta/SMTP endpoints.
5. No test failures, flaky tests or orphan rows were observed in either suite run. Both suites, tsc and the admin build were clean on the first run.
6. The dry run's stray `outstanding: 420` pending cleanup jobs in the shared test database (item above) is not a Wave 2 defect, but a future cleanup of the disposable stack itself (or a periodic reset) would keep that count meaningful for the next person who runs this probe.
