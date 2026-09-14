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
