# Community Spotlight handoff for review (2026-09-13)

This document lets a reviewer with no prior context audit everything built for Ahavah Community Spotlight on 2026-09-13: what it is, where every artefact lives, how to run it, what the reviews found and what was ruled, and what is still open. Nothing described here has been pushed or deployed. Repos are under `D:/Antigravity/`.

## 1. What was built, in one paragraph

Spotlight lets members opt in to be featured on the Ahavah Facebook Page and Instagram (new-member welcome, weekly newcomers roundup, member of the week, ad hoc highlight), each card approved by the member before it posts, plus three member emails (launch announcement with opt-in, weekly community email replacing the digest, a 30-day re-invite naming new members that match the reader's preferences) and an admin Growth tab on admin.ahavah.app. Phase A built consent, emails, stats and links. Phase B built the posting engine: a publishing queue in Postgres, a Graph API worker and daily tick as Vercel crons in the admin app, card approval emails, removals, retention, attribution. Group posting is impossible (Meta removed the Groups API in April 2024) and stays a manual share of the Page post.

## 2. Authority documents (read in this order)

1. Spec, binding: `ahavah-api/docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (amended during Phase A: E1 and E3 honour the notifications unsubscribe, E2 cap is 6 days, section 2 records the unsubscribe fixes).
2. Phase A plan: `ahavah-api/docs/superpowers/plans/2026-09-13-community-spotlight-phase-a.md` (13 tasks, verbatim code).
3. Phase B plan: `ahavah-api/docs/superpowers/plans/2026-09-13-community-spotlight-phase-b.md` (12 tasks; tasks 4 to 11 were outline level in the plan and were expanded into full briefs before dispatch, see section 4).
4. Pre-flight record: `ahavah-api/docs/superpowers/plans/2026-09-13-spotlight-preflight.md` (owner items, Vercel plan check, droplet base URL, the dead `/u/` link finding).
5. Design brief sent to Claude Design: `ahavah-web/docs/design-briefs/2026-09-13-community-spotlight.md` (also at `briefs/2026-09-13-community-spotlight.md` in the design project). The design has not been executed yet; three tasks wait on it (section 7).

## 3. Branches and commits (none pushed)

Base branches: API `ahavah/main`, web `master`, admin `master`. Pushing any base branch deploys production (API via GitHub Actions to the droplet; web and admin via Vercel). Phase B branches are stacked on Phase A; merge order is A then B.

| Repo | Branch | Base | Head | Commits |
| --- | --- | --- | --- | --- |
| ahavah-api | spotlight-phase-a | 2361e35 | 2d38d30 | 16 (`git log --oneline ahavah/main..spotlight-phase-a`) |
| ahavah-web | spotlight-phase-a | 5615407 | fd19ce9 | 7 |
| ahavah-api | spotlight-phase-b | 2d38d30 | 3d6cad8 | 14 (`git log --oneline spotlight-phase-a..spotlight-phase-b`) |
| ahavah-admin | spotlight-phase-b | 0f25e31 | 98f3a09 | 4 |
| ahavah-web | spotlight-phase-b | fd19ce9 | 7728165 | 2 |

Commit subjects follow `type(scope): sentence`, no trailers. The admin repo's working tree holds two pre-existing uncommitted files (`src/components/admin/tab-users.tsx`, `user-drawer-economy.tsx`, an em-dash cleanup by the owner) that are not part of this work.

## 4. Where the per-task evidence lives

Each phase has a git-ignored workspace with the ledger, one brief and one report per task, review packages (the exact diff each reviewer read), and the fix-wave reports:

- Phase A: `ahavah-api/.superpowers/sdd/2026-09-13-community-spotlight-phase-a/` (`progress.md` ledger; `task-N-brief.md`, `task-N-report.md`; `review-<base>..<head>.diff`; `final-fix-brief.md`, `final-fix-report.md`, `followups-report.md`). Web-side packages for Phase A are in `ahavah-web/.superpowers/sdd/2026-09-13-community-spotlight-phase-a/`.
- Phase B: `ahavah-api/.superpowers/sdd/2026-09-13-community-spotlight-phase-b/` (same layout; `task-2-brief.md` through `task-11-brief.md` are the expanded briefs; `final-fix-brief.md`, `final-fix-report.md`, `targeted-fix-report.md`). Admin and web packages: `ahavah-admin/.superpowers/sdd/2026-09-13-community-spotlight-phase-b/`, `ahavah-web/.superpowers/sdd/2026-09-13-community-spotlight-phase-b/`.

The ledgers record every ruling as `Ruling: <decision> - <why> - <cost if wrong>`, every deferred minor, and every fix round with commit ranges. Start there.

## 5. How to run the gates

- API (disposable Docker stack, from `ahavah-api`): `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q`. Expected on 3d6cad8: 383 passed, 9 known Pydantic deprecation warnings. Always pass the `tests` path (a bare run collects two pre-existing broken files elsewhere). Never run two API suites at once: the stack shares one Postgres and the connection lock is not reentrant. A hang means a nested `api_tx`; stop the run container with `docker rm -f`. New migrations are applied to a warm stack with `docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d duo_api -v ON_ERROR_STOP=1 < migrations/<file>` (the postgres service has no `/app` mount). `docker-compose.test.yml` now sets `SESSION_TOKEN_SECRET` and `AHAVAH_GROWTH_CRON_SECRET=test-cron-secret`.
- Admin (from `ahavah-admin`): `node --test tests/*.test.mjs` (35 pass on 98f3a09), `npx tsc --noEmit`, `pnpm exec next build`. Tests transpile TS with `ts.transpileModule` and run in a `vm` sandbox with stubbed `fetch` (see `tests/auth-session.test.mjs` for the pattern; `tests/publishing.test.mjs` overrides module exports such as `sleep`).
- Web (from `ahavah-web`): `pnpm test` (544 on 7728165), `npx tsc --noEmit`, `npx eslint <files> --max-warnings=0`. Husky runs lint-staged on commit.

## 6. Map of the code

### Phase A, API
- `migrations/0039_community_spotlight.sql` (consent columns on `person`, `email_send_log`, `campaign_link`, `campaign_click`), `0040_community_unsubscribe.sql`.
- `service/campaigns/__init__.py`: `can_send` (7-day cap, per-run idempotency), `log_send`, `campaign_unsubscribed`, `unsubscribed_predicate_sql`, `suppressed_predicate_sql`, `make_campaign_link` (same-origin unless `external_ok`, allowlist `ALLOWED_EXTERNAL_HOSTS`), `record_click`, `_ua_class`. `service/campaigns/runner.py`: `run_campaign` (suppression, scope-unsubscribe skip, cap, build, send, log, `post_send`; failure returns partial counts with `error`).
- `service/growth/queries.py`: `growth_stats`, `dormant_cohort`, `count_reinvite_cohort`, `newcomers_since`, `count_newcomers_since`, shared `_last_action_sql` and `_newcomer_predicate_sql`, exclusions (`admin@ahavah.app` plus env `AHAVAH_TEST_ACCOUNT_EMAILS`), `post_stats`.
- `service/spotlight/__init__.py`: signed confirm tokens (HMAC, 30-day TTL), `set_spotlight_opt_in` (cancels queued cards on opt-out, Phase B hook). `service/api/spotlight_routes.py`: `GET /spotlight/confirm/<token>` (read-only), `POST` (opts in).
- `service/api/campaign_link_routes.py`: `GET /s/<key>` (counts a click, 302). `service/api/unsubscribe_routes.py`: `GET /u/<token>` now renders a form; `POST` stamps.
- Emails: `emails/spotlight_announcement.py` + `send_spotlight_announcement.py` (E1), `emails/community_weekly.py` + `send_community_weekly.py` (E2, scope `community`, `CAP_DAYS = 6`), `emails/reinvite.py` + `send_reinvite.py` (E3). Module contract: `recipients()`, `recipient_count()`, `build_for(row)`, `FROM_ADDR`, `UNSUB_SCOPE`, optional `preview_row`, `post_send`. All member-supplied text is escaped.
- `service/api/admin/growth_routes.py`: `GET /admin/growth/stats`, `GET /admin/growth/emails`, `POST /admin/growth/emails/<campaign>/preview|send` (campaign ids, audit rows, `CAP_DAYS`).
- `service/person/sql/__init__.py`: `Q_GET_PROFILE_INFO` exposes `spotlight_opt_in`.

### Phase A, web
- `src/app/s/[key]/route.ts` (307 to `/api/s/<key>`; Phase B adds the cookie), `src/app/u/[token]/route.ts` (GET and POST 307 to `/api/u/<token>`; before this, every emailed unsubscribe link 404ed), `src/lib/legal-spotlight-copy.ts` used by the four legal pages, nudge removal in `src/lib/next-action.ts` and `use-next-action.ts`.

### Phase B, API
- `migrations/0041_spotlight_queue.sql` (`publishing_queue`, `spotlight_removal_task`, `spotlight_setting`, `campaign_click.signup_person_id`, `claim_spotlight_posts`), `0042_roundup_snapshot.sql` (`payload jsonb`), `0043_spotlight_indexes_and_retry.sql` (`campaign_link(kind)` index; claim function reclaims `failed` rows with attempts below 3).
- `service/spotlight/eligibility.py` (ordered reasons), `queue.py` (candidates, member approval, transitions, `cancel_for_member` including roundup tiles and removal tasks, `reap_expired_leases`, settings), `roundup.py` (tile snapshot), `approval.py` (card tokens, `card_state`), `storage.py` (`delete_images`, batched), `attribution.py` (`attribute_signup`).
- `service/api/cron_auth.py` (`require_admin_or_cron`, header `X-Growth-Cron` compared with `hmac.compare_digest`). `service/api/admin/spotlight_routes.py`: the queue endpoints (`/admin/growth/queue*`, `/candidates`, `/spotlight/welcome|roundup|member-of-week|suggest`, `/settings`, `/removals*`, `/token-health`); admin-or-cron routes use unauthenticated decorators with a hand-rolled `_session()` that mirrors `require_auth` (signed_in and person_id checks) and a dedicated limiter (scope `growth`, 120/min, cron exempt); human-only routes use `aget`/`apost` with `require_admin`. `service/api/spotlight_card_routes.py`: `GET/POST /spotlight/card/<token>` (member approve or skip).
- Emails: `emails/spotlight_card_ready.py` (E4), `emails/spotlight_card_live.py` (E5, share link via `make_campaign_link(external_ok=True)`). Cron: `service/cron/spotlightretention/`.

### Phase B, admin
- `src/lib/growth-server.ts` (server-only API client with the cron header), `src/lib/publishing.ts` (`publishDue`, `publishRow`, `processRemovals`, `reportTokenHealth`, `cronAuthorised`; only Facebook `photos` and Instagram `media_publish` count as "attempted"), `src/lib/tick.ts` (`runTick`), `src/lib/spotlight-card.ts` (STUB renderer that throws; Task 5 replaces it), routes under `src/app/api/growth/{publish-due,tick,token-health}/route.ts`, `vercel.json` crons, `.env.example`.

### Phase B, web
- `src/app/s/[key]/route.ts` sets cookie `ahavah.spotlight_ref`; `src/lib/spotlight-ref.ts`; finish-onboarding sends `spotlight_ref`.

## 7. What is not done and why

- Task 5 (card renderer with `next/og`), Task 10 (Growth tab queue, member of the week and controls panels), Task 4b (web card approval page), and Phase A's Task 7b (privacy switch, confirm page UI) and Task 11 (Growth tab stats and emails panels UI): all wait for the Claude Design export of the brief in section 2, per the project's rule that new surfaces come from Claude Design. Their briefs exist for the API halves; UI halves are transcribed from the design when it lands.
- Task 12 / Task 13 (deploy, announcement send, first real post): owner actions. Pre-flight items still open: Meta app permissions (`pages_manage_posts`, `instagram_content_publish`) for Page `1100237303180442` and Instagram `17841447302854202`; Page token and the six env variables in the admin Vercel project; `AHAVAH_GROWTH_CRON_SECRET` on the droplet and in Vercel; the admin role on the owner's account. Closed: Vercel team is Pro (per-minute crons allowed); droplet `AHAVAH_WEB_BASE_URL` is `https://ahavah.app`; no email waves are scheduled on the droplet.
- The announcement email (E1) must not be sent before the confirm page exists (Task 7b).

## 8. Findings the reviews caught (for the reviewer's calibration)

Each was found by a task review or the whole-branch review and fixed with a test:
- Phase A: `GET /u/<token>` stamped unsubscribes on GET (mail scanners) and the web app had no `/u/` route at all; the admin send endpoint threw after mail had gone out; member names reached email HTML unescaped; stats totals counted admin and test accounts; the re-invite headline count ignored age preferences; the weekly cap would have skipped the weekly send; `GET /admin/growth/emails` deadlocked on a nested transaction.
- Phase B: the hand-rolled session check skipped `signed_in` (pre-OTP bearer bypass); E5 share links were rejected by the same-origin guard; dry runs claimed and mutated rows; roundup rows bypassed consent at claim and on opt-out; the weekly email could republish an opted-out member; captions carried no campaign link; `countries` had two shapes across API and admin; removals ran with the scheduler off; the growth routes were unthrottled and then shared the unsubscribe limiter with the worker's own tick.

## 9. Parked follow-ups (not blocking, recorded in the ledgers)

`delete_images` runs inside the transaction (move after commit); an orphan PNG can remain after a rejected upload; Instagram permalinks are placeholders until the media shortcode is stored; `MAX_ATTEMPTS` lives in both SQL and Python; `GET /admin/growth/queue` is capped at 200 rows without a cursor; the empty-string poll env pattern crashes cron containers at import (pre-existing convention); cards render only on the daily tick, not at approval; no GIN index on `publishing_queue.payload`; the `claim` response keeps a v1 array shape until the admin client opts into `shape: v2`; the cron secret has no rotation window.

## 10. Suggested review method

1. Read both ledgers end to end; every ruling is a decision made without the owner and is the first thing to challenge.
2. Diff each branch against its base with the packages in section 4 or `git diff <base>..<head>`.
3. Run the three suites (section 5).
4. Spot-check the consent invariants directly: `eligibility()` reason order; `cancel_for_member` on a member who is a roundup tile; `_Q_SPOTLIGHT` in the weekly email; `claim` with the scheduler off; a dry `publishDue` making no `claim` call.
5. Spot-check the auth surface: `_session()` versus `service/api/decorators.py` `require_auth`; `is_cron_request`; the limiter on every admin-or-cron route; `GET` handlers never writing.
6. Confirm the three-way contract (API routes, `growth-server.ts`, `publishing.ts`/`tick.ts`): paths, bodies, `due=1`, `shape`, `countries` as an integer.

## 11. Wave 1 (2026-09-14): consent and lifecycle invariants

An adversarial review of the branches above (2026-09-14, twelve findings F01 to F12) found that the phase A and B work described in sections 1 to 10 did not close consent and lifecycle: a member's approval was not bound to the exact card that would post, the final check before publishing could pass on missing information, deletion could bypass withdrawal, a late confirmation after withdrawal could be lost, one channel's publish blocked the other, confirm links could be replayed after withdrawal, and the on/off controls did not do what their names implied. Wave 1 remediates F01 to F05, F11 and the control-model part of F12. F06 to F10, and the remainder of F12, are deferred to Wave 2 and Wave 3.

### Branches and heads

| Repo | Branch | Base | Task 9 final commit | Head after the fix wave |
| --- | --- | --- | --- | --- |
| ahavah-api | spotlight-wave-1 | 381c275 (spotlight-phase-b head) | 7094181 | the fix-wave commit below |
| ahavah-admin | spotlight-wave-1 | 98f3a09 (spotlight-phase-b head) | 85d6dd6 | the fix-wave commit below |

The `cab499e` / `aa1341c` heads this table carried when it was first written
were Task 9's implementation commits, not its final ones: Task 9's review
round landed `7094181` (api) and `85d6dd6` (admin) on top, and `20843f8`
(api) added the acceptance evidence. The fix wave below sits on those.

Neither branch is merged or pushed. Web is untouched in Wave 1.

### Commits

API (`git log --oneline 381c275..HEAD`, oldest first):

1. `aa5c9e7` feat(db): spotlight revisions, consent, occurrences, nonces, delivery state (0044)
2. `5f41f87` feat(spotlight): immutable revisions and revision-bound approval
3. `bc13af9` feat(spotlight): fail-closed dispatch check bound to lease, revision and consent
4. `20d3ec4` fix(spotlight): terminal requests immutable, one render per revision, review transition
5. `223969e` feat(spotlight): one withdrawal operation from every lifecycle exit
6. `03f30c0` feat(spotlight): lease-bound receipts and delivery state separate from withdrawal
7. `e9a0d57` fix(spotlight): scoped roundup re-issue, idempotent withdrawal counts, cron pass intersection
8. `c620bf6` feat(spotlight): feature occurrences as the cooldown unit, E5 once per card
9. `1a505ab` fix(spotlight): re-issue repoints only non-terminal rows
10. `b552867` fix(spotlight): roundup late receipts file one unattributed task, stricter lease token, audit on recorded only
11. `9965eb3` feat(spotlight): single-use confirm and card tokens bound to the consent epoch
12. `fa1098d` feat(spotlight): roundups are count-only unless tiles are enabled; tiled roundups need every participant's consent
13. `5d19038` fix(spotlight): consume the nonce only after the decision lands; skip mints for unknown recipients
14. `c7cbab6` fix(spotlight): roundup participants belong to the route, not create_candidate
15. `cab499e` feat(spotlight): three honest controls, welcome cohort by sign-up time, weekly roundup key

Admin (`git log --oneline 98f3a09..HEAD`, oldest first):

1. `8cc0d01` feat(admin): lease-bound completion, delivery_unknown, receipt retries reported in the body
2. `93cb7e5` feat(admin): instagram permalink in the publish receipt
3. `aa1341c` feat(admin): publish, removals and tick read the three named controls

### Test totals

- API (disposable Docker stack, `tests -q`): 490 passed (up from the 383 baseline before Wave 1), 9 known deprecation warnings.
- Admin (`node --test tests/*.test.mjs`): 44 passed (up from the 35 baseline before Wave 1); `npx tsc --noEmit` clean; `next build` clean.

These are the totals as of the ninth of eleven Wave 1 tasks landing (below). The tenth task (this document) and the eleventh (the acceptance run) do not change the counts above on their own.

### Findings F01 to F12

| Finding | Status | Resolved by | Notes |
| --- | --- | --- | --- |
| F01 approval not bound to the exact card | Resolved in Wave 1 | Task 2 (`5f41f87`, fix `20d3ec4`); Task 8 (`fa1098d`, fix `c7cbab6`) for roundups | Consent is now per content revision; any material edit creates a new revision with no consent carried over. |
| F02 final check fails open | Resolved in Wave 1 | Task 3 (`bc13af9`) | The dispatch check now fails closed on lease, status, revision, consent, the exact photo, and both publication controls. |
| F03 deletion bypasses withdrawal | Resolved in Wave 1 | Task 4 (`223969e`, fix rounds `e9a0d57`, `1a505ab`) | One withdrawal operation is now called from opt-out, account deletion, admin delete or ban, the pending-deletion cron and moderation actions. |
| F04 late receipt after withdrawal is lost | Resolved in Wave 1 | Task 5 (api `03f30c0`, fix `b552867`; admin `8cc0d01`) | A late published receipt after withdrawal is recorded and files a removal task immediately rather than being dropped. |
| F05 first platform blocks the second | Resolved in Wave 1 | Task 6 (`c620bf6`) | The 30-day cooldown now reads a feature occurrence shared by both channel rows of one card, instead of a single stamp the first channel's publish set. |
| F06 rejected upload can overwrite approved bytes | Deferred to Wave 2 | Not yet resolved; a related guard (`already_rendered`) landed in Task 2 (`5f41f87`, fix `20d3ec4`) | Full remediation is content-hashed, immutable object keys (triage remediation item 6). |
| F07 mail dedupe not durable | Deferred to Wave 2 | Not resolved | A durable outbox with reservation as the idempotency point (triage remediation item 7). |
| F08 E4 on a daemon thread | Deferred to Wave 2 | Not resolved | Folded into the same durable outbox work as F07. |
| F09 cleanup loses retry info; removals paused with the scheduler | Partly resolved in Wave 1, remainder deferred to Wave 2 | Task 9 (api `cab499e`, admin `aa1341c`) for the pause behaviour; cleanup job durability and retry tracking not resolved | Removals now run whenever the emergency stop is off, independent of the publication control; the cleanup job's own retry bookkeeping is triage remediation item 8. |
| F10 attribution without a matching click | Deferred to Wave 3 | Not resolved | Attribution rebuilt on visitor-bound click receipts, sequenced after the design-gated surfaces per the triage document. |
| F11 confirm token replay after withdrawal | Resolved in Wave 1 | Task 7 (`9965eb3`, fix `5d19038`) | Confirm and card tokens carry a single-use nonce bound to the person's consent epoch; a withdrawal burns unused nonces and replay answers 410 `stale`. |
| F12 controls diverge | Control-model part resolved in Wave 1, remainder deferred to Wave 3 | Task 9 (api `cab499e`, admin `aa1341c`) | The three named controls, the sign-up-time welcome cohort and the weekly business key are done; the auto flags are removed rather than left unused. Idempotent-tick work beyond the weekly key, and any auto mode, wait on Wave 3 per the triage sequencing. |

### Fix wave (2026-09-15)

A whole-branch review of everything above found seven defects, two of them
record-loss paths that could leave a withdrawn member visible on the Page.
One commit per repo closes them, on the same two branches, still unpushed:
`fix(spotlight): recoverable receipts, purge respects in-flight rows, tile
photos, approvals gate on member of the week, standing preference cleared on
withdrawal` (api) and `fix(admin): refused published receipts are reported,
tick renders every row needing a render` (admin). Their SHAs, both suite
totals and the observed failing tests are in
`.superpowers/sdd/2026-09-14-spotlight-wave-1/fix-wave-report.md`.

1. A live post whose receipt never landed is now recoverable, and withdrawal
   never orphans it. `record_receipt` accepts a late `published` outcome on a
   row parked in `review` with `delivery_state` `attempting` or
   `delivery_unknown`, under the lease that produced it; the operator route
   `POST /admin/growth/queue/<id>/reconcile` (admin session only) does the
   same with no lease, for the case where no lease holder will ever return;
   `withdraw_member` stamps such a row and files an `investigate` removal
   task instead of cancelling it and deleting its artwork; and the admin
   worker reports a refused `published` receipt in `unrecorded` rather than
   silently counting it `stale`.
2. `POST /admin/growth/queue/purge` stamps every row it touches but cancels
   only what withdrawal itself would cancel, leaving `processing` rows to
   their lease holder and unresolved `review` rows alone. It answers
   `cancelled` and `left_attempting`.
3. A roundup tile is checked against its own photo: the tile snapshot and the
   revision participants carry `photo_uuid`, and the dispatch check answers
   `participant:<id>:photo_missing` for a participant without one.
4. `POST /admin/growth/spotlight/member-of-week` carries the same
   `approvals_enabled` gate and `invite_sent` audit field as the welcome
   route, so no E4 goes out while approvals are paused.
5. Choosing a different photo on the card link no longer burns the nonce: the
   route answers `{ok, result: 'new_revision', revision}` and the same link
   still works once the new revision is rendered.
6. The tick lists `needs_render=1` with no status filter, so an
   `awaiting_member` row that still needs artwork is rendered.
7. Withdrawal clears `spotlight_opt_in` for every reason, so reactivating an
   account does not quietly resume featuring the member.
8. Minors in the same commits: the complete route locks the queue row before
   reading the value it gates E5 on; `is_first_confirmation` takes the row
   lock over the request's rows before counting (the process lock is per
   gunicorn worker and orders nothing between workers); `pictured_people`
   reads the revision through the row's own `current_revision_id`; the E5
   recipient query requires `activated AND spotlight_opt_in`; the dispatch
   and occurrence tests restore the seeded settings in a `finally`; and the
   em dashes in `service/api/admin/users_action_routes.py` and
   `service/person/__init__.py` are gone.

### Where the record lives

- Wave 1 briefs, task reports, review packages and the ledger: `ahavah-api/.superpowers/sdd/2026-09-14-spotlight-wave-1/` (`progress.md` is the ledger; `task-N-brief.md` and `task-N-report.md` per task; `review-<base>..<head>.diff` and `review-admin-<base>..<head>.diff` are the exact diffs each reviewer read).
- The triage that scoped Wave 1: `ahavah-api/docs/superpowers/plans/2026-09-14-spotlight-adversarial-remediation-triage.md`.
- The Wave 1 plan: `ahavah-api/docs/superpowers/plans/2026-09-14-spotlight-wave-1.md`.
- Reversed rulings recorded against the phases they reversed: `ahavah-api/.superpowers/sdd/2026-09-13-community-spotlight-phase-a/progress.md` and `.../phase-b/progress.md`, each under a "Rulings reversed by the 2026-09-14 adversarial review" heading.

At the time this section was written, task 9 (the control model above) had been implemented and its review was still in progress; every other Wave 1 task through task 9 was complete per the ledger, this task (task 10, the spec and ledger amendments) was underway, and task 11 (the acceptance run) had not started.

### Activation stance

Nothing from Wave 1 is merged and nothing is pushed; the phase A, phase B and Wave 1 branches all stay local. No live post happens until the review's own acceptance matrix (Consent, Lifecycle, Delivery, Storage, Mail, Controls) passes in a staging environment. Wave 1 closes the Consent, Lifecycle, Delivery and Controls rows; Storage and Mail wait on Wave 2, so the matrix cannot pass end to end until that work lands too.
