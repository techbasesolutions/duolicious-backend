# Community Spotlight: local acceptance run against the release matrix

Date: 2026-09-16 (probe timestamps read 2026-09-17 UTC). Source of the gates: "Release acceptance matrix" in `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md`, read together with its findings F01 to F12.

This is a local run on the disposable Docker stack. It is evidence, not a fix: nothing in application code was changed. No production host was contacted, no Meta API was called and no real mail was sent.

Heads run against:

| Repo | Branch | Head |
| --- | --- | --- |
| `ahavah-api` | `spotlight-wave-3c` | `dde0ef8` |
| `ahavah-web` | `spotlight-wave-3c` | `e452c8a` |
| `ahavah-admin` | `master` | `30daef1` |

## Summary

| Gate | Verdict | One-line reason |
| --- | --- | --- |
| Consent | **Partial, one sub-claim FAILED** | A member who approves from a tab showing revision 1 has consent recorded on revision 2, which the browser never showed. |
| Lifecycle | Partial | Opt-out and self-deletion in all eight queue states leave no untracked post (driven end to end); no moderation action exists to test. |
| Delivery | **Partial, one sub-claim FAILED** (same defect as Storage 5a) | Two channels, separate runs, partial failure, lost confirmation, worker death and late receipts all reconcile; the real operator approve route cannot schedule a rendered card (see Storage), and real Graph timeouts are not exercised. |
| Mail | **Partial, one sub-claim FAILED** | Restart, death mid-send, acceptance uncertainty and send-time consent proven; a duplicate concurrent submit answers 500 part-way through its cohort, and real SMTP is not exercised. |
| Storage | **Partial, one sub-claim FAILED** | `storage.make_public` raises `AttributeError` on the real boto3 object, so approving any rendered card answers 503; its unit test passes against a stub that invents the method. |
| Operator controls | Partial, pending owner ruling on the invite-pause copy | Invite pause, publication pause, emergency stop, absent auto flags and visible deadlines behave as labelled through the real worker and API. |
| Real platform | Needs staging or owner | Account IDs, ownership, token scopes, image format, permalinks and removal need the owner's Meta access. |
| Runtime | **Partial, three sub-claims FAILED** | Two concurrent ticks create two welcome requests and two E4s; the 200-row listings starve old work; slow Graph exceeds the 60 s budget with no checkpoint. |
| Measurement | Proven locally | Web click route to receipt to attribution; bots, replays, forged and expired receipts earn nothing; per-platform totals reconcile. |
| Deployment | **Partial, one sub-claim FAILED** (no runbook) | Migrations apply and re-apply cleanly on a fresh disposable database and every suite and build is green; a production-copy migration, secrets and config need the owner, and no rollback or cron-pause runbook exists in any repo. |
| First live exercise | Needs staging or owner | Owner-gated by definition, and blocked by the failures above. |

Count: **1 Proven locally, 8 Partial, 2 Needs staging or owner.** Sub-claims that FAILED: Consent (stale-tab approval), Delivery (operator approve through the real route, same defect as Storage), Mail (duplicate concurrent submit answers 500), Storage (`make_public`), Runtime (duplicate welcome ticks, roundup duplicates answering 500, backlog starvation, execution budget), Deployment (no rollback or cron-pause runbook).

Corrected 2026-09-16 after the whole-branch review: the first version labelled the Delivery, Mail and Deployment failures as plain Partial, Operator controls as Proven, and softened the roundup 500s. The Meta documentation findings in section 7 were also added then.

## How the run was made

### Suites at the heads

| Command | Result |
| --- | --- |
| API, from `ahavah-api`: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` | `655 passed, 9 warnings in 34.91s` (the 9 are the known Pydantic deprecation notices) |
| API, the Spotlight, outbox, growth, attribution, cron and migration files verbosely (same command with those 31 test files and `-v -p no:warnings`), so each test cited below can be read as PASSED | `363 passed in 62.85s`, zero FAILED or ERROR lines |
| Admin, from `ahavah-admin`: `node --test tests/*.test.mjs` | `tests 112, pass 112, fail 0` |
| Admin: `npx tsc --noEmit` | exit 0 |
| Admin: `npx next build` | `Compiled successfully`, `Generating static pages (6/6)`, exit 0 |
| Web, from `ahavah-web`: `pnpm test` | `Test Files 56 passed (56)`, `Tests 587 passed (587)` |
| Web: `pnpm exec tsc --noEmit` | exit 0 |
| Web: `pnpm exec eslint <the four files changed d042eab..e452c8a> --max-warnings 0` | exit 0 |
| Web: `pnpm exec next build` (the `prebuild` cache bump deliberately skipped so the tree stays clean) | `Compiled successfully`, `Generating static pages (77/77)`, exit 0 |
| API, the committed Wave 2 probe: `docker compose ... run --rm ... api -c "cd /app && PYTHONPATH=/app python tests/_evidence/wave2-dry-run-probe.py"` | exit 0; output quoted under Mail and Storage |

### The integrated stack and probes

Two probe files were written for this run. Neither is collected by any test runner.

- `ahavah-api/tests/_evidence/acceptance_helper.py`: database-side helper (make a synthetic member or admin with a session, read rows back, drain the outbox with a stub SMTP client, kill a drain mid-send, call `attribute_signup` exactly as finish-onboarding does).
- `ahavah-admin/tests/_evidence/acceptance-local-probe.mjs`: the driver. It loads the real admin worker (`src/lib/publishing.ts`), the real daily tick (`src/lib/tick.ts`) and the real `next/og` card renderer, talks to the real API over HTTP, and opens the real web card page in headless Chrome.

Stack, all local:

```
# ahavah-api (postgres was already up)
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml up -d s3mock redis
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run -d --name ahavah-acceptance-api -p 5000:5000 \
  -v /d/Antigravity/ahavah-api:/app -e REDIS_HOST=redis -e PYTHONPATH=/app --entrypoint bash api \
  -c "cd /app && python3 database/initapi.py && exec gunicorn --workers 4 --bind 0.0.0.0:5000 --timeout 0 service.api:app"

# ahavah-web (MSYS_NO_PATHCONV matters: without it Git Bash rewrote NEXT_PUBLIC_API_BASE_URL=/api
# into a Windows path and every browser call failed)
MSYS_NO_PATHCONV=1 AHAVAH_API_ORIGIN=http://127.0.0.1:5000 pnpm exec next dev -p 3107

# ahavah-admin
node tests/_evidence/acceptance-local-probe.mjs      # all sections; or name sections
```

The API runs under gunicorn with four workers, the production default (`DUO_WORKERS` defaults to 4 in `api.main.sh`). That matters: `database.api_tx` holds one connection per process, so a single-process dev server would serialise every request and hide the races found under Runtime.

What is stubbed, and only at the network boundary:

- Graph: an in-process stub for `graph.facebook.com` that fetches the image URL it is handed from the local bucket, the way Meta would, and records caption and bytes.
- The public image host `https://user-images.ahavah.app/<key>` is answered from the local s3mock bucket. The renderer's member photo fetch is answered with a flat colour per member.
- The member preview link: the local API presigns against the compose host `http://s3mock:9090`, which the page's own CSP blocks (`img-src` plus `upgrade-insecure-requests`). The browser sees the link re-hosted on an https `*.digitaloceanspaces.com` name, the shape `img-src` allows; the bytes still come from s3mock. The CSP is not altered.
- SMTP: the helper drains the outbox with a stub client.
- Every other outbound host is refused and recorded. Every run ended with `OBSERVED refused outbound hosts :: []`.

The shared test database already holds about 34,000 queue rows and 8,000 open removal tasks from earlier suite runs. The probe renders only its own synthetic members (named `Acc<run><tag>`) and reports other rows as skipped, and the Graph stub never deletes a post it did not publish.

One probe-level continuation is disclosed here and in the probe's own comments: because `make_public` fails (see Storage), the operator approve route answers 503 and reverts the row to `review`. To keep testing everything downstream of approval, the probe then applies the exact write the route makes on success (`status = 'scheduled'`, `scheduled_for`) and nothing else. Every such approval is counted in the output (`operator approvals that failed in make_public and were continued by the probe :: 32` in the full run).

The full run (run id `uncr9`, 3 min 12 s) ended `EXPECTATIONS: 100 ok, 7 failed`. The seven failures are the ones named in this document. Two short section re-runs are cited where they add something: `controls` (run `uxk0s`, one added observation) and `mail` (run `uklgx`, the concurrent-submit check, which the full run skipped because the probe's own members had grown the e1 cohort past its 60-person guard).

Screenshots from the browser steps are in `.superpowers/sdd/2026-09-16-spotlight-wave-3c/shots/task-4/` (git-ignored scratch; regenerate with the probe).

## 1. Consent

Matrix: "Browser shows final revision; approve/skip are POST; edits revoke approval; roundup needs each participant; chosen-photo deletion blocks dispatch."

### 1a. Browser shows the final revision: FAILED

What is proven, in a real browser against the real API: the card page shows exactly the bytes of the current rendered revision, offers no approval while a new revision is unrendered, and shows the new bytes once rendered. Consent then lands on the revision that was shown.

```
EXPECT ok   A: browser shows the bytes of revision 1 :: {"state":"default","shown":"037a999a3440","rev1":"037a999a3440"}
EXPECT ok   A: loading the page (GET) recorded no consent
EXPECT ok   A: caption edit makes revision 2, unrendered, no consent :: {"status":200,"revision":2}
EXPECT ok   A: page offers no approval while revision 2 is unrendered :: "unavailable"
EXPECT ok   A: browser now shows the bytes of revision 2, not revision 1 :: {"shown":"1621566ea126","rev2":"1621566ea126"}
EXPECT ok   A: approve is a POST and binds consent to revision 2 :: {"status":200,"consent":[{"revision_id":18349,"revision":2,...}]}
```

And later, the bytes Graph fetched are the same bytes the browser showed:

```
EXPECT ok   A: the bytes Graph fetched are the approved revision 2 bytes the browser showed :: [{"platform":"facebook","status":200,"sha":"1621566ea126"},{"platform":"instagram","status":200,"sha":"1621566ea126"}]
```

What failed: the stale tab. Member C opens the card link (the page shows revision 1, caption "Welcome to Ahavah, ..."). An operator rewrites the caption and the tick renders revision 2. Member C, still looking at revision 1, presses Approve.

```
OBSERVED C: tab still shows :: {"shown":"3596ae42b2a7","rev1":"3596ae42b2a7","rev2_now_current":"2fbb578415b0","rev2_caption":"Operator rewrote this caption for Accuncr9C https://get.ahavah.app/s/-8H0nJ-w"}
OBSERVED C: POST from the stale tab :: {"status":200,"body":{"ok":true,"result":"approved"},"page_state":"approved","consent":[{"revision_id":18350,"revision":2,"person_id":56742,"role":"subject"}]}
EXPECT FAIL C: consent is never recorded against a revision the browser did not show :: {"browser_showed_revision":1,"consent_recorded_on_revision":[2]}
```

Screenshots: `consent-C-1-rev1-open-tab.png` (revision 1 on screen) and `consent-C-2-stale-tab-approved.png` ("Approved. We will email you when it is live.").

Why: the page's POST body is `{ decision: "approve", photo_uuid }` (`ahavah-web/src/app/spotlight/card/[token]/page.tsx`, `handleApprove`). It carries no revision number or asset hash. `approve_card` (`service/spotlight/revisions.py`) records consent on whatever revision is current when the POST arrives, as long as the photo is unchanged. A caption-only edit keeps the photo, so the member consents to wording and artwork they never saw. F01's acceptance ("preview bytes and caption match the dispatched revision") is not met.

What should have happened: the POST names the revision (or asset hash) the page displayed, and a mismatch is answered the way a photo change already is (`new_revision`, re-show the card, ask again).

Two related observations, not failures: the member sees the caption only as rendered inside the card (clamped to two lines); the posted message text with its link is never shown as text. The E4 email says the member can "choose the photo you prefer", but the photo picker is deferred (Wave 3 owner decision 2).

### 1b. Approve and skip are POST: Proven locally

Browser: loading the page (GET) recorded no consent; Approve and Skip are POSTs (`A: approve is a POST...` above; `EXPECT ok B: skip is a POST that cancels both rows and records no consent :: {"first_state":"default","status":200}`; screenshot `consent-B-2-skipped.png`). Tests, all PASSED in the verbose run: `tests/test_spotlight_card.py::test_get_is_read_only_and_post_approves`, `::test_post_approve_is_gated_on_settings_and_render`, `::test_post_skip_cancels_and_wrong_email_is_403`, `tests/test_spotlight_consent.py::test_confirm_get_is_read_only_and_reports_stale`.

### 1c. Edits revoke approval: Proven locally

Browser: a caption edit made revision 2 with no consent and an unrendered card (above). Tests PASSED: `tests/test_spotlight_revisions.py::test_caption_edit_creates_new_revision_and_drops_consent`, `::test_different_photo_makes_new_revision_without_consent`, `::test_edit_of_in_flight_row_is_refused`, `::test_create_revision_refuses_a_terminal_request`; `tests/test_spotlight_card.py::test_choosing_another_photo_keeps_the_card_link_usable`.

### 1d. Roundup needs each participant: Proven locally (fail closed)

Tests PASSED: `tests/test_spotlight_routes.py::test_roundup_route_with_tiles_needs_every_participant`, `::test_roundup_eligible_checks_every_tile_member`, `tests/test_spotlight_revisions.py::test_welcome_consent_does_not_satisfy_roundup`, and the `consent_incomplete` case of `tests/test_spotlight_dispatch.py::test_each_condition_fails_closed`.

Note for the owner: no route anywhere records a `participant` consent (`grep -rn "record_consent(" service/` finds only the `subject` call in `revisions.py`). A tiled roundup can therefore never be dispatched; only the count-only roundup can. That matches the standing owner decision (tiles off), but turning `roundup_tiles_enabled` on today would produce roundups that sit in the queue forever.

### 1e. Chosen-photo deletion blocks dispatch: Proven locally

Tests PASSED: `tests/test_spotlight_dispatch.py::test_deleting_chosen_photo_fails_even_with_another_approved_photo`, `::test_hard_deleted_subject_fails_closed`, `::test_tile_whose_own_photo_was_deleted_fails_even_with_another_approved_photo`, `::test_participant_without_a_photo_uuid_fails_closed`.

### Needs the owner

The browser preview only loads if production presigns against an https host that the web CSP's `img-src` allows (`https://*.digitaloceanspaces.com` is listed in `ahavah-web/next.config.ts`). Locally it did not (the compose host is blocked, which is why the probe re-hosts the link). The owner should confirm `DUO_BOTO_ENDPOINT_URL` on the droplet is a `*.digitaloceanspaces.com` https endpoint, and staging should load a real preview.

## 2. Lifecycle

Matrix: "Opt-out, deletion and moderation during every queue state leave no untracked publication; late receipts cause removal."

### 2a. Opt-out and self-deletion in every queue state: Proven locally

The probe made 16 members, one per (state, reason) pair, drove each to its state through the real tick, card POST, operator approval, worker and Graph stub, then withdrew through the real member routes (`PATCH /profile-info {"spotlight_opt_in": false}` or `DELETE /account`). For `processing`, the withdrawal was made from inside the in-flight Graph publish call.

States reached before withdrawal (excerpt):

```
OBSERVED before withdrawal scheduled/opt_out :: ["facebook:scheduled:none","instagram:scheduled:none"]
OBSERVED before withdrawal processing/opt_out :: ["facebook:published:published","instagram:review:none"]
OBSERVED before withdrawal delivery_unknown/opt_out :: ["facebook:review:delivery_unknown","instagram:review:delivery_unknown"]
OBSERVED before withdrawal lease_expired/opt_out :: ["facebook:review:attempting","instagram:review:attempting"]
OBSERVED before withdrawal failed/opt_out :: ["facebook:review:none","instagram:failed:failed"]
OBSERVED before withdrawal published/opt_out :: ["facebook:published:published","instagram:published:published"]
```

(`awaiting_member` and `review` likewise, and each state for `account_deletion`.) After withdrawal the worker ran three more times with publication on:

```
OBSERVED sweep after withdrawal :: [{"claimed":0,"published":0},{"claimed":0,"published":0},{"claimed":0,"published":0}]
EXPECT ok   no Graph publish call for any withdrawn member after withdrawal :: 0
```

Then, for every one of the 16 cases, three invariants held: no published row without a removal task naming its post; no unresolved delivery without an open task and no row left claimable or awaiting; no Spotlight mail left queued. For example:

```
EXPECT ok   processing/opt_out: no published row without a removal task naming its post :: {"rows":["facebook:published:published","instagram:review:none"],"tasks":["facebook:delete_via_api:ext"]}
EXPECT ok   failed/account_deletion: no published row without a removal task naming its post :: {"rows":["facebook:cancelled:none","instagram:cancelled:failed"],"tasks":[]}
EXPECT ok   published/account_deletion: no published row without a removal task naming its post :: {"rows":["facebook:published:published","instagram:published:published"],"tasks":["facebook:delete_via_api:ext","instagram:manual_instagram:ext"]}
```

The removals worker then deleted the Facebook posts and left Instagram for a human:

```
OBSERVED processRemovals :: {"deleted":3,"left":189,"reported":8,"needs_attention":0,"halted":false}
OBSERVED tasks after removals published/opt_out :: ["facebook:delete_via_api:done","instagram:manual_instagram:open"]
```

Admin ban, admin deactivate, admin hard delete and the pending-deletion cron were not driven through the state matrix; their tests PASSED: `tests/test_spotlight_withdrawal.py::test_every_status_is_handled` (6 parametrised cases), `::test_opt_out_withdraws`, `::test_self_delete_route_withdraws`, `::test_admin_ban_withdraws_before_delete`, `::test_admin_deactivate_route_withdraws`, `::test_admin_hard_delete_route_withdraws`, `::test_pending_deletion_cron_withdraws_before_hard_delete`, `::test_removal_task_survives_person_delete`, `::test_withdrawal_clears_the_standing_preference`.

Observation: when the member withdraws during the first platform's publish call, the sibling row's eligibility check answers `withdrawn` and the worker parks it in `review` with `delivery_state = none` and the cancellation stamp (see `processing/opt_out` above). It is never claimable again, so nothing is posted, but it sits in the operator's review list indefinitely.

### 2b. Moderation during every queue state: not proven

`withdraw_member` accepts `reason='moderation'`, but no production code path calls it (unchanged since the Wave 1 evidence). There is no moderation action to drive. What would prove it: a real moderation action (hide, ban from review) wired to `withdraw_member`, then this probe's state matrix run with that action as a third reason. That needs an owner decision on which moderation actions withdraw.

### 2c. Late receipts cause removal: Proven locally

Dead worker: the probe claimed four rows directly, never completed them, backdated the lease, and the next claim reaped them into `review`/`attempting`. After withdrawal (an `investigate` task each), the dead worker's original lease reported `published`:

```
OBSERVED dead worker claimed :: 4
OBSERVED next claim reaped :: {"reaped":4,"paused":true}
OBSERVED late receipt from the dead worker (opt_out, facebook) :: {"status":200,"body":{"already":false,"ok":true,"status":"published"}}
EXPECT ok   lease_expired/opt_out: no published row without a removal task naming its post :: {"rows":["facebook:published:published","instagram:published:published"],"tasks":["instagram:manual_instagram:ext","facebook:delete_via_api:ext"]}
```

Operator reconcile of a withdrawn `delivery_unknown` row upgraded its task the same way (`delivery_unknown/*` lines above). The in-flight `processing` case filed its task from the late receipt. Tests PASSED: `tests/test_spotlight_delivery.py::test_late_receipt_after_withdrawal_is_recorded_and_removal_filed`, `::test_late_receipt_on_a_roundup_files_one_unattributed_task`, `::test_reaped_row_records_a_late_published_receipt`.

## 3. Delivery

Matrix: "Two channels succeed for one occurrence; timeout/ack loss/worker death produce reconcilable attempts rather than duplicate posts."

### 3a. Two channels succeed for one occurrence: FAILED through the approve route; proven after scheduling

Same run (member A):

```
OBSERVED publishDue (A) :: {"claimed":2,"published":2,"failed":0,"review":0,"delivery_unknown":0,...}
EXPECT ok   A: both platform rows published in one run :: [{"platform":"facebook","status":"published","post_url":"https://www.facebook.com/probe-page_uncr91"},{"platform":"instagram","status":"published","post_url":"https://www.instagram.com/p/igmediauncr93/"}]
EXPECT ok   A: Graph received exactly two publishes, one per platform :: ["facebook","instagram"]
EXPECT ok   A: the caption Graph received is the queue row caption carrying the edited text :: ["Edited caption for Accuncr9A https://get.ahavah.app/s/r32lBxjC?p=facebook","Edited caption for Accuncr9A https://get.ahavah.app/s/r32lBxjC?p=instagram"]
EXPECT ok   A: one occurrence for the request, not one per platform :: [{"person_id":56740,"kind":"welcome"}]
EXPECT ok   A: E5 queued once :: ["e4:queued","e5:queued"]
```

Instagram then Facebook, across separate runs, with a partial failure (member D):

```
OBSERVED D runs :: {"run1":{"claimed":1,"failed":1,"published":0},"run2":{"claimed":1,"published":1},"run3":{"claimed":1,"published":1,"review":0}}
EXPECT ok   D: run 1 Instagram failed before publishing, run 2 Instagram published, run 3 Facebook published :: ["facebook:published:attempts=1","instagram:published:attempts=2"]
EXPECT ok   D: one occurrence, E5 once
```

Three concurrent worker runs over the same due rows published each platform exactly once (Runtime, 8c). Tests PASSED: `tests/test_spotlight_occurrence.py::test_sibling_channel_passes_after_first_publishes`, admin `facebook row publishes and completes with the lease token and post url`, `instagram receipt carries the permalink`.

But the operator's real approve step failed for every row, which is why the probe had to continue it:

```
EXPECT FAIL A: operator approve facebook -> scheduled through the real route :: {"status":503,"body":{"error":"storage_unavailable","in_flight":false},"probe_applied_route_write":true}
```

The cause is recorded under Storage. Until it is fixed, no rendered card can be scheduled through the Growth tab, so this sub-claim is proven only for the path after scheduling.

### 3b. Timeout, ack loss and worker death reconcile without duplicate posts: Proven locally for the mechanics

Lost confirmation (the Graph stub reset the connection after the publish call was sent) parked both rows as `delivery_unknown`, never failed or retried: `OBSERVED drive delivery_unknown :: {"claimed":4,"published":0,...,"delivery_unknown":4}`. Worker death is the lease reap in 2c. Neither was re-claimed in the three-run sweep. Tests PASSED: `tests/test_spotlight_delivery.py::test_delivery_unknown_parks_and_is_never_reclaimed`, `::test_wrong_or_missing_lease_rejected`, `::test_duplicate_receipt_is_noop`, `tests/test_spotlight_routes.py::test_claim_reaps_expired_leases`; admin `lost confirmation is reported as delivery_unknown, never as failed or published`, `a stale lease leaves the row alone and does not retry the receipt`, `a receipt that never lands is reported with the external id in the body, not as published`.

Not proven: how real Graph behaves on a timeout (whether a post exists), which is the case `delivery_unknown` exists for. That needs staging against the real accounts.

## 4. Mail

Matrix: "Concurrent submits, process death and acceptance uncertainty are handled; suppression is checked; E4/E5 survive restart."

### 4a. E4/E5 survive restart: Proven locally

The probe created a welcome (E4 queued in the same transaction), sent `SIGKILL` to the API container, started it again, then drained:

```
OBSERVED E4 across a SIGKILL of the API :: {"welcome_status":200,"queued_before_kill":["queued"],"health_after_polls":3,"first_drain":{"result":{"reserved":1,"accepted":1,...},"sent":[{"to":"acceptance-probe-...@ahavah-test.invalid","subject":"Your Spotlight card is ready"}]},"second_drain":{"result":{"reserved":0,...},"sent":[]}}
EXPECT ok   M1: exactly one E4 sent after restart, none on a second drain
```

E5 is queued inside the receipt transaction by the same outbox; the probe saw it queued exactly once per occurrence (3a). Tests PASSED: `tests/test_spotlight_card.py::test_e4_is_enqueued_in_the_candidate_transaction_and_survives_restart`.

### 4b. Process death and acceptance uncertainty: Proven locally

The helper reserved a message, "sent" it and killed its own process before the acceptance write:

```
OBSERVED drain death :: {"exit_code":9,"after_death":["e4:reserved"],"drain_before_timeout":{"result":{"reserved":0,...},"sent":[]},"reap":{"backdated":1,"result":{...,"unknown_reaped":1},"sent":[]},"after_reap":["e4:acceptance_unknown"],"listed_on_unknown_surface":{"campaign":"e4","campaign_id":"e4-e305c96c7900447bb3b6c2274a2de7d9","n":1}}
EXPECT ok   M2: the stranded message becomes acceptance_unknown, is never re-sent, and is listed for an operator
```

The committed Wave 2 probe, re-run at this head, shows the same: `drain (reaps a lost reservation) -> {"reserved": 0, "accepted": 0, "skipped": 0, "failed": 0, "unknown_reaped": 1}`, `smtp3.sent (must be empty) -> []`, `lost row state -> acceptance_unknown`. Tests PASSED: `tests/test_email_outbox.py::test_a_drain_that_dies_mid_send_strands_exactly_one_row`, `::test_a_send_that_was_never_recorded_is_never_sent_again`, `::test_status_counts`; `tests/test_growth_routes.py::test_unknown_mail_summary_lists_every_run_with_acceptance_unknown_rows`.

### 4c. Suppression checked at send: Proven locally

Consent was cleared underneath a queued E4, then drained: `{"drain":{"result":{"reserved":1,"accepted":0,"skipped":1,...},"sent":[]},"rows":["e4:skipped:withdrawn"]}`, `EXPECT ok M3: nothing sent to a member who is no longer opted in`. Tests PASSED: `tests/test_email_outbox.py::test_suppression_and_unsubscribe_checked_at_send_time`, `::test_an_address_suppressed_after_enqueue_is_never_sent`, `::test_the_frequency_cap_is_rechecked_at_send_time`.

### 4d. Concurrent submits: FAILED (a duplicate submit answers 500)

Two simultaneous `POST /admin/growth/emails/e1/send` with the same `campaign_id` (run `uklgx`, four API workers):

```
OBSERVED e1 dry run :: {"built":39,...}
OBSERVED concurrent submits :: {"responses":[{"status":200,"queued":39},{"status":500}],"people":39,"max_rows_per_person":1}
EXPECT ok   e1: concurrent submits never queue a person twice :: {"people":39,"built":39}
```

Proven: nobody is queued twice (the unique outbox key holds). Not clean: the losing request died with `psycopg.errors.SerializationFailure: could not serialize access due to concurrent update` inside `outbox.enqueue` (API log), so it answered 500 part-way through its cohort and wrote no audit row. The route still processes a whole cohort inside one HTTP request, which F07's remedy asked to replace with a resumable job. Tests PASSED: `tests/test_email_outbox.py::test_concurrent_drains_never_double_send`, `::test_enqueue_is_the_idempotency_point`.

Not proven: real SMTP provider acceptance, bounces and provider-side idempotency. Needs staging with the real provider.

## 5. Storage

Matrix: "Immutable asset identity; concurrent edit safe; corrupt images rejected; failed deletion retried with retained keys."

### 5a. Making an approved card public: FAILED

`service/spotlight/storage.py::make_public` calls `_bucket().Object(key).put_object_acl(ACL='public-read')`. A boto3 `s3.Object` resource has no such method. Reproduced on the real stack:

```
docker exec ahavah-acceptance-api python -c "import service.spotlight.storage as st; k='spotlight/probe-acl-test.png'; st._bucket().put_object(Key=k, Body=b'x', ACL='private', ContentType='image/png'); st.make_public(k)"

  File "/app/service/spotlight/storage.py", line 146, in make_public
    _bucket().Object(key).put_object_acl(ACL='public-read')
AttributeError: 's3.Object' object has no attribute 'put_object_acl'
```

And independent of any storage endpoint:

```
docker exec ahavah-acceptance-api python -c "import boto3; o = boto3.resource('s3', region_name='us-east-1', aws_access_key_id='x', aws_secret_access_key='y').Object('bucket','key'); print('boto3', boto3.__version__, 'has put_object_acl:', hasattr(o,'put_object_acl'), 'has Acl:', hasattr(o,'Acl'))"
boto3 1.35.99 has put_object_acl: False has Acl: True
```

`requirements.txt` pins `boto3==1.35.99`, the version production builds with. The error is raised before any network call, so it will happen against Spaces too. `post_growth_queue_approve` catches it as a storage failure, reverts the row to `review` and answers `503 storage_unavailable`; all 32 operator approvals the full run made answered that way.

Why the suite is green: `tests/test_spotlight_storage.py::test_make_public_sets_public_read_acl` PASSED, but its `_Object` stub defines its own `put_object_acl` method ("just enough to record a `put_object_acl` call the way `make_public` makes it"), so it tests the stub's shape, not boto3's. The route tests monkeypatch `make_public` away entirely. An ad hoc `mypy` over the Spotlight modules did not catch it either, because boto3 ships no type stubs.

What should have happened: approve schedules the row and the object becomes publicly readable (on the resource API that is `Object(key).Acl().put(ACL='public-read')`, or the client's `put_object_acl(Bucket=..., Key=..., ACL=...)`). What would prove it: a test against boto3's real resource or client shape (for example botocore's Stubber) and an approve that returns 200 on staging Spaces.

### 5b. Immutable identity, concurrent edit safety, corrupt images, retained keys: Proven locally

Tests PASSED: `tests/test_spotlight_routes.py::test_image_route_uses_content_hashed_key_and_private_acl`, `tests/test_spotlight_assets.py::test_attach_platform_image_refuses_once_the_revision_is_rendered`, `tests/test_spotlight_routes.py::test_image_route_superseded_when_revision_changes_mid_upload`, `tests/test_spotlight_storage.py::test_validate_png_rejects_oversize` (and the parametrised `test_validate_png_rejects`), `tests/test_spotlight_routes.py::test_image_route_rejects_invalid_png`, `tests/test_spotlight_storage.py::test_delete_images_never_requests_quiet_mode`, `tests/test_spotlight_cleanup.py::test_partial_confirmation_retains_unconfirmed_keys`, `::test_abandon_after_max_attempts`, `::test_abandoned_job_rows_are_listed_on_the_removals_endpoint`.

Integrated: every probe render landed on a content-hashed key per revision (`spotlight/<request_key>/<revision_id>-<sha prefix>-<platform>.png`, visible in the card JSON), a caption edit produced a new object with a different hash rather than overwriting, and the bytes Graph fetched matched the revision's `asset_hash` (3a). The committed Wave 2 probe at this head: `run_cleanup_batch (partial confirmation) -> {"reserved": 2, "done": 1, "retried": 1, ...}` with the unconfirmed key still `pending`, and `run_cleanup_batch (halted) -> {..."halted": true, "outstanding": 1528}`, `delete calls made while halted -> []`.

### Needs staging

Private-by-default objects on real Spaces, the public ACL flip once 5a is fixed, CDN caching of a replaced key, and real deletion confirmation. s3mock does not enforce ACLs, so a private object was readable locally (`OBSERVED A: public image object after approve :: {"status":200,...}` even though the approve had failed).

## 6. Operator controls

Matrix: "Invite pause, publication pause and emergency stop behave as labeled; auto flags either work or are absent; cleanup deadlines visible."

### Verdict: Partial, pending owner ruling on the invite-pause copy

Through the real tick, worker and API (run `uxk0s`):

```
EXPECT ok   invite pause: tick creates no card and no E4 for a new member :: {"invites_paused":true,"rows":0}
EXPECT ok   publication pause: nothing claimed, no Graph call :: {"claimed":0,...,"paused":true,"halted":false,...}
EXPECT ok   publication pause: removals still run (Facebook deleted, Instagram left for a human) :: {"published":2,"tasks":["facebook:delete_via_api:done","instagram:manual_instagram:open"],...}
EXPECT ok   emergency stop: publish, removals and token check make no Graph call :: {"haltedPub":true,"haltedRem":{...,"halted":true},"haltedToken":{"expires_at":null,"valid":false,"halted":true},"graph_calls":0}
EXPECT ok   emergency stop: outstanding and overdue counts stay visible :: {"overdue":127,"outstanding_cleanup":1527,"halted":true}
EXPECT ok   deadlines: an overdue manual Instagram removal is counted and its deadline is on the row :: {"before":127,"after":128,"deadline_at":"2026-09-17T00:35:10.160285+00:00"}
EXPECT ok   approvals pause: member approval refused, the 7-day clock paused :: {"approve":{"status":409,"body":{"error":"approvals_disabled"}},"expire":{"approvals_paused":true,"cancelled":0}}
```

Auto flags are absent: `tests/test_spotlight_controls.py::test_legacy_keys_gone_and_rejected` PASSED. Other tests PASSED: `::test_invites_gate_candidates_and_creation`, `::test_claim_reports_paused_and_halted`, `::test_removals_halted_by_emergency_stop_only`, `tests/test_spotlight_cleanup.py::test_overdue_removals_counted`, `::test_emergency_stop_halts_cleanup_and_counts_outstanding`; admin `publish is paused when the api says paused`, `publish is halted by the emergency stop`, `removals run while publication is paused`, `removals stop under the emergency stop`, `token health honours the emergency stop`.

One observation for the owner to rule on, which keeps this gate at Partial until ruled: an E4 queued before the invite pause is still sent while the pause is on (`OBSERVED invite pause: an E4 queued before the pause, drained during it :: {"sent":1,...}`). The outbox drain does not read `invites_enabled`. The switch copy says "no card-ready email is sent" and also "Existing cards keep moving", so whether this is as labelled depends on which sentence governs.

The Growth tab's rendering of these controls was proven by screenshot in Wave 3 and was not re-shot.

## 7. Real platform

Matrix: "Verify Page and Instagram account IDs, ownership/connection, token scopes, media acceptance, actual permalink and removal process against the configured accounts."

### Verdict: Needs staging or owner

Nothing here can be proven without the owner's Meta access, and no Meta call was made. Open, each exactly as stated in the brief:

- Page and Instagram account IDs, and that the Instagram account is connected to that Page. The handoff records Page `1100237303180442` and Instagram `17841447302854202`; neither was checked.
- Token scopes and expiry for the Page token (`AHAVAH_META_PAGE_TOKEN`).
- Meta's image-format acceptance for the chosen Instagram path. The renderer produces PNG (1080 by 1080, validated by `validate_png`); whether the Instagram content publishing path accepts PNG is unverified. The adversarial review explicitly declined to certify it, and so does this run.
- The actual permalink: the worker asks `GET <media id>?fields=permalink` and Facebook's URL is built from the post id; both are stubbed here.
- The removal process: Facebook `DELETE <post id>` and the Graph error codes the worker treats as "already gone" (code 100 with subcode 33, or a 404 carrying code 100) and as permission errors (10, 200) are unverified against real responses. Instagram has no API delete; those tasks go to a human.

Confirmed from Meta's own documentation on 2026-09-16 (no API call made). These are known code changes required before a live post, not open questions for the owner:

- Instagram content publishing accepts JPEG only. The worker uploads the PNG card, so every Instagram publish would fail.
- On `POST /{page-id}/photos`, `message` is deprecated in favour of `caption`; the worker sends `message`.
- `GET /debug_token` needs an app access token (or an app developer's token); the daily token check sends the Page token itself, so its answer cannot be trusted.
- The worker's default Graph version `v21.0` is available until 2027-01-21; the current version is `v26.0`.

What would prove it: the owner, with Meta Business access, confirms the IDs and connection, runs `debug_token` on the Page token for scopes, and a staging run publishes one PNG card to each platform, reads back the permalink and deletes or hides it.

## 8. Runtime

Matrix: "Worst-case Graph polling fits execution budget or resumes from a checkpoint; duplicate/missed cron invocations reconcile; backlog pagination cannot starve old work."

### 8a. Duplicate cron invocations: FAILED for welcomes, FAILED for roundups (3 of 8 calls answer 500 though one occurrence results), clean for publishing

Two ticks at once for one new member, against four API workers:

```
OBSERVED concurrent ticks :: {"a":{"welcomes_created":1,"welcomes_skipped":0,"rendered":0},"b":{"welcomes_created":1,"welcomes_skipped":0,"rendered":2}}
EXPECT FAIL T1: duplicate ticks produce one welcome request and one E4 :: {"request_keys":2,"e4":2}
```

Eight concurrent welcome calls for another member:

```
OBSERVED welcome burst statuses :: [200,200,200,200,409,409,409,409]
EXPECT FAIL T2: a burst of duplicate welcome calls converges on one request and one E4 :: {"request_keys":4,"e4":4}
```

The API log shows four `cron growth.queue.welcome` audit lines for the same `person_id` with four different request keys. Why: `post_growth_spotlight_welcome` checks for an existing non-cancelled welcome and then calls `create_candidate`, which mints a fresh random `request_key`; nothing unique stops two workers passing the check together. The member would receive two (or four) "Your Spotlight card is ready" emails for two cards. F12's acceptance asks for a defined occurrence key for welcomes and for duplicate ticks to converge. Vercel documents duplicate cron invocations, so this is reachable in production.

Eight concurrent roundup calls in the same ISO week:

```
OBSERVED roundup burst :: {"statuses":[200,500,500,500,200,200,200,200],...,"rows":2,"revisions":1,"links":1}
EXPECT ok   roundup: one logical occurrence (two platform rows, one revision, one link)
EXPECT FAIL roundup: every duplicate call answers cleanly (no 5xx) :: [200,500,500,500,200,200,200,200]
```

The weekly business key converges to one occurrence; the losers hit `psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "publishing_queue_request_key_platform_key"` and answer 500. The tick treats that as "not created", so the cron log would show failures for a roundup that exists.

Three concurrent publish runs over the same due rows, which were due two hours earlier (a missed minute catching up):

```
OBSERVED concurrent publish runs :: [{"claimed":2,"published":2,"stale":0},{"claimed":2,"published":2,"stale":0},{"claimed":0,"published":0,"stale":0},{"followup_claimed":0,"published":0}]
EXPECT ok   U1: each platform published exactly once across concurrent runs :: {"rows":["facebook:published","instagram:published"],"graph_publish_calls":2}
EXPECT ok   U1: one occurrence and one E5
```

(U2 the same.) Concurrent ticks also raced on one image upload (`SerializationFailure` in `attach_platform_image`, answered 500); the other tick's upload attached and the card was rendered once, so that race converges.

### 8b. Missed cron invocations: Partial

- A missed publish minute catches up: rows due two hours earlier were claimed and published (above).
- A missed daily tick recovers for welcomes, because candidates are anyone who signed up in the last 14 days without a live welcome.
- A missed Monday roundup does not recover on its own: `roundup_due` is true only on a Monday (`OBSERVED candidates today :: {"weekday_utc":4,"roundup_due":false}`). It can be recovered deliberately, because `POST /admin/growth/spotlight/roundup` creates the current week's roundup on any day (the burst above ran on a Thursday UTC), but only with curl: the Growth tab has no roundup action.

### 8c. Backlog pagination cannot starve old work: FAILED

Both worker listings are capped at 200 rows, newest first. On the shared test database:

```
OBSERVED needs_render: qualifying rows vs rows the tick is handed :: {"qualifying":1117,"oldest_qualifying":"2026-09-15 04:33:11.059859+00:00","handed_to_tick":200}
OBSERVED pending removal tasks: due vs handed to the worker :: {"due":8198,"oldest_due":"2026-09-14 00:38:21.058479+00:00","handed_to_worker":200,"oldest_handed":"2026-09-17T01:20:39.983855+00:00"}
```

Controlled demonstration: member V's welcome was created unrendered, then 205 newer rows that can never render were inserted.

```
OBSERVED ticks with the poison backlog :: [{"rendered":0,"render_failed":200},{"rendered":0,"render_failed":200}]
EXPECT FAIL V: an older card is rendered despite 205 newer unrenderable rows :: {"asset_hash":null}
OBSERVED V after the poison rows are deleted and one more tick runs :: {"rendered":true}
```

Why: `_Q_ROWS` (the tick's `needs_render` listing) and `_Q_REMOVALS` (the worker's `pending=1` drain) both end `ORDER BY ... created_at DESC LIMIT 200` in `service/api/admin/spotlight_routes.py`. For renders, 200 newer rows that keep failing hide every older card indefinitely. For removals it is worse: with more than 200 due tasks the oldest, most overdue removals are never handed to the worker at all, while the 72-hour deadline runs. The Growth tab reads the same 200-row queue listing. This is the known open item, now reproduced; the claim itself (`claim_spotlight_posts`) orders oldest first and is not affected.

### 8d. Graph polling fits the execution budget or resumes: FAILED under slow Graph

`publish-due` has `maxDuration = 60`, claims two rows by default, allows each Graph call 15 s, polls an Instagram container up to five times with 2 s between polls, then publishes and reads the permalink. The probe ran the real worker on a scaled virtual clock (1 real ms = 200 virtual ms) with a Graph stub of fixed latency:

```
OBSERVED 2 Instagram rows, Graph answers in 0.3 s, container ready on first poll :: {"virtual_seconds":2,"within_60s":true,"published":2,...}
OBSERVED 2 Instagram rows, Graph answers in 2 s, container ready on the 5th poll :: {"virtual_seconds":50,"within_60s":true,"published":2,...}
OBSERVED 1 Instagram row, Graph answers in 10 s, container ready on the 5th poll :: {"virtual_seconds":89,"within_60s":false,"published":1,...}
OBSERVED 2 Instagram rows, Graph answers in 14.9 s (just under the 15 s timeout), ready on first poll :: {"virtual_seconds":120,"within_60s":false,"published":2,...}
```

Normal latency fits. Slow latency does not, and there is no checkpoint to resume from: a run cut off by the platform leaves its rows `processing` until the 10-minute lease expires, then they are reaped into `review` for a human (2c shows that path and that it never double posts). Safe, but not "fits or resumes". What would prove the real margin: Graph latencies and container processing times measured on staging. The daily tick (`maxDuration = 300`, up to 200 render groups) was not timed.

## 9. Measurement

Matrix: "Visitor-bound seven-day conversion tests; per-platform totals reconcile; no false credit without a valid click."

### Verdict: Proven locally

Through the real web click route (`next dev`, `/s/<key>?p=...`) to the real API:

```
OBSERVED clicks through the web route :: {"fbVisitor":{"status":307,"receipt":true},"igVisitor":{"status":307,"receipt":true},"agentless":{"status":307,"receipt":false},"crawler":{"status":307,"receipt":false},"rows":[{"platform":"facebook","ua_class":"desktop","has_receipt":true},{"platform":"instagram","ua_class":"desktop","has_receipt":true},{"platform":"facebook","ua_class":"bot","has_receipt":false},{"platform":"facebook","ua_class":"bot","has_receipt":false}]}
EXPECT ok   a browser click through the web route gets a receipt cookie distinct per visitor
EXPECT ok   an agentless click and a crawler click get no receipt
EXPECT ok   a valid receipt credits once; a replay and a forged value credit nothing
EXPECT ok   the bare caption key credits no click (it still stamps spotlight_ref until the compatibility branch is removed)
OBSERVED post_stats :: {"clicks":2,"signups":1,"by_platform":{"facebook":{"clicks":1,"signups":1},"instagram":{"clicks":1,"signups":0},"unknown":{"clicks":0,"signups":0}}}
EXPECT ok   per-platform totals reconcile to the unique totals, bots excluded
EXPECT ok   a receipt older than seven days credits nothing :: {"credited":false,"spotlight_ref":null}
```

Redemption was made with `attribute_signup`, the exact call `post_finish_onboarding` makes; the onboarding HTTP flow itself was not driven. Tests PASSED: `tests/test_spotlight_attribution.py::test_no_click_gets_no_credit`, `::test_an_expired_receipt_gets_no_credit`, `::test_one_visitors_receipt_cannot_be_claimed_by_another`, `::test_a_receipt_is_single_use`, `::test_losing_a_concurrent_claim_declines_instead_of_failing_the_signup`, `::test_post_stats_splits_clicks_and_signups_by_platform`, `::test_a_bot_receipt_does_not_exist_to_be_claimed`, `::test_first_touch_wins_and_does_not_consume_the_second_receipt`.

Notes, none of which is false credit: the legacy branch still lets the bare caption key stamp `person.spotlight_ref` (it credits no click, and no count reads `spotlight_ref`); its removal is held on `spotlight-legacy-ref-removal` until 2026-09-22 and was not run here. `by_platform` still has no HTTP surface, so an operator cannot see the split outside a query.

## 10. Deployment

Matrix: "Migrations on a disposable copy; build/lint/type/tests on final commits; secrets/config validated without exposing values; explicit rollback and cron-pause runbook."

### 10a. Migrations on a disposable database: Proven locally (fresh schema), production copy needs the owner

A fresh Postgres from the stack's own image, initialised by the API's own init path, then the deploy's own migration script twice:

```
docker run -d --name ahavah-acceptance-pg-fresh --network ahavah-api_default -e POSTGRES_PASSWORD=password -e POSTGRES_USER=postgres -e POSTGRES_DB=postgres ahavah-api-postgres postgres -c shared_preload_libraries=pg_stat_statements
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm --no-deps -v /d/Antigravity/ahavah-api:/app -e DUO_DB_HOST=ahavah-acceptance-pg-fresh -e PYTHONPATH=/app --entrypoint bash api -c "cd /app && python3 database/initapi.py"
  -> Created database: duo_api ... Finished initializing api DB   (exit 0)
MSYS_NO_PATHCONV=1 AHAVAH_POSTGRES_CONTAINER=ahavah-acceptance-pg-fresh bash scripts/apply-deploy-migrations.sh
  -> pass 1: exit 0, 49 "Applying" lines, last "Applying 0049_cleanup_job_updated_at.sql"
  -> pass 2: exit 0, 0 "Applying", 49 "Already applied"
tracker rows: 49; spotlight_setting: approvals_enabled=false, external_access_enabled=true, invites_enabled=true, publication_enabled=false, roundup_tiles_enabled=false
```

The container was removed afterwards. Not proven: the same script against a copy of production data. The local test database cannot stand in for that (its tracker has the known local-only 0046 checksum drift recorded in the handoff). The owner can take a `pg_dump` of production into a disposable instance and run the script there.

### 10b. Build, lint, type and tests on the final commits: Partial

Green: API suite 655, admin 112 with `tsc` and `next build`, web 587 with `tsc`, `next build` and eslint on the Wave 3c files (table above). Gaps: the API has no lint or type gate in CI. An ad hoc `mypy --follow-imports=silent service/spotlight service/api/admin/spotlight_routes.py service/campaigns/outbox.py` in a throwaway container reported `Found 13 errors in 3 files` (for example `spotlight_routes.py:1295: Argument 2 to "set_status" has incompatible type "UUID"; expected "str"`, `withdrawal.py:264: Value of type "dict[Any, Any] | None" is not indexable`, and two missing boto3 stubs); none is known to be a runtime fault, and it did not catch 5a. The repo-wide web eslint debt recorded in Wave 3 was not re-run. The held branch `spotlight-legacy-ref-removal` was not tested.

### 10c. Secrets and config validated without exposing values: Needs owner

Nothing about production configuration can be checked locally. Known open from the Wave 3c ledger: `AHAVAH_GROWTH_CRON_SECRET` is not yet set on the droplet or in Vercel (the owner has not approved it), so the growth crons answer 401. Still to confirm by the owner, by name only: the six admin Vercel variables (`AHAVAH_FB_PAGE_ID`, `AHAVAH_IG_USER_ID`, `META_GRAPH_VERSION`, `CRON_SECRET`, `AHAVAH_GROWTH_CRON_SECRET`, `AHAVAH_API_ORIGIN`) plus `AHAVAH_META_PAGE_TOKEN`, the droplet's `SESSION_TOKEN_SECRET` and object-store variables, and `DUO_BOTO_ENDPOINT_URL` being an https `*.digitaloceanspaces.com` endpoint (see Consent, "Needs the owner").

### 10d. Explicit rollback and cron-pause runbook: FAILED (does not exist)

Searched every Markdown file under `ahavah-api/docs`, `ahavah-admin` and `ahavah-web/docs` for rollback, runbook and cron pause. Nothing Spotlight-specific exists. The handoff has a "Deploy order, and why it is not reversible" section (API before web) but no rollback steps. A generic code rollback for the API droplet and for the web Vercel project exists only in a private session memory note, not in any repo; it does not cover the admin app, the forward-only migrations, or pausing the three Vercel crons. The in-app controls (publication pause, emergency stop) are the nearest thing to a cron pause, but they do not stop the tick from creating candidates and E4s (only `invites_enabled` does), and nothing documents the order to flip them in an incident.

What would close it: a written runbook in the API repo covering, in order, the emergency stop and invite pause, disabling or unscheduling the admin Vercel crons, rolling back each of the three deploys, and what cannot be rolled back (migrations, posts already live, mail already accepted).

## 11. First live exercise

Matrix: "Owner/member-approved test post only after the preceding gates; verify both platform receipts and the actual removal/withdrawal path."

### Verdict: Needs staging or owner

Owner-gated by definition. It is also blocked today: a card cannot be scheduled through the real approve route (5a), consent can bind to an unseen revision (1a), and the real-platform gate is untouched. The probe's local stand-in for this exercise (3a and 2a) passed for everything downstream of scheduling.

## Before the first live post

Ordered by what blocks what.

1. **Make approval schedule a card at all (Storage 5a).** Every operator approval of a rendered card fails with 503 today, so nothing downstream can happen through the product. Replace the stub-shaped test with one against boto3's real resource or client shape.
2. **Bind member approval to the revision the page showed (Consent 1a).** Before `approvals_enabled` is turned on, or a stale tab can approve content the member never saw.
3. **Make welcome creation converge under duplicate ticks, and roundup duplicates answer cleanly (Runtime 8a).** Before the growth crons are authorised, or members get duplicate cards and duplicate E4 emails.
4. **Stop the 200-row listings starving old work (Runtime 8c).** Oldest first or paginated, for the removal drain above all: withdrawal removals are a promise with a deadline.
5. **Owner: set `AHAVAH_GROWTH_CRON_SECRET` and the admin Vercel variables (Deployment 10c).** Nothing runs until then; do it after 1 to 4 so the crons start on fixed code.
6. **Write the rollback and cron-pause runbook (Deployment 10d).** Needed before activation, not after the first incident.
7. **Decide the slow-Graph policy (Runtime 8d)**: fewer rows per run, a checkpoint, or explicit acceptance that a slow run parks rows for a human.
8. **Fix the four Meta documentation findings (Real platform 7)**: JPEG for Instagram, `caption` on Page photos, an app access token for `debug_token`, Graph `v26.0`.
8b. **Owner: Meta verification (Real platform 7)**: IDs and connection, token scopes, permalinks, removal behaviour.
9. **Staging run of this matrix on the exact deploy heads**, including this probe against staging, real Spaces (ACL, CDN, preview under the CSP), real SMTP, real Graph latency and a production-data copy for migrations (Storage, Mail, Delivery, Deployment 10a).
10. **Owner rulings that do not block but should be recorded**: whether moderation actions withdraw (Lifecycle 2b); whether an E4 queued before an invite pause should still send (Controls); roundup tiles stay off until a participant-consent route exists (Consent 1d).
11. **First live exercise (11)**: one owner or member-approved test post on both platforms, verify both receipts, then the real withdrawal and removal path.
