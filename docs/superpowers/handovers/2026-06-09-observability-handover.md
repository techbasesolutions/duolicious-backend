# Observability + Admin Monitoring — Handover

**Date written:** 2026-06-09
**Status:** **DESIGN + PLAN COMPLETE, IMPLEMENTATION NOT YET STARTED.** Next agent should execute the plan top to bottom.
**Spec:** [`docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md`](../specs/2026-06-09-observability-and-admin-monitoring-design.md)
**Implementation plan:** [`docs/superpowers/plans/2026-06-09-observability-and-admin-monitoring-implementation.md`](../plans/2026-06-09-observability-and-admin-monitoring-implementation.md)

## TL;DR for the next developer

The spec and plan in the links above are ready to execute. The plan is six phases (~13 hours total), TDD-first, with exact file paths, complete code snippets, and exact verification commands at every step. Read this handover for context on WHY the work exists and HOW it slots into the existing codebase, then dispatch the plan to subagents (recommended) or execute inline.

**Suggested approach:**

1. Read this handover end-to-end (~10 min).
2. Read the spec (~15 min).
3. Skim the plan's File Structure section + Phase 6 self-review table to know what lands where.
4. Use `superpowers:subagent-driven-development` (one subagent per task, two-stage review per task).
5. After Phase 4 lands, the dashboard tiles render real data — that's the natural "ship" point if you want to stop short of the full 6 phases.

## Why this work exists (the 2026-06-08 incident)

On 2026-06-08 ~23:01 UTC a gunicorn worker in `ahavah-api` was killed by `SIGSEGV`. The crash was triggered by `psycopg.errors.UniqueViolation` on `search_cache_pkey`. Two concurrent `/search` calls for the same user raced through `Q_UNCACHED_SEARCH_1` (DELETE — both saw 0 rows, neither acquired a lock) then `Q_UNCACHED_SEARCH_2` (INSERT — the second crashed on the primary key violation).

The race was made reachable in production by an `ahavah-web` change (commit `7a2a65d`, `useDiscoverDeck` force-flag) that started firing concurrent `/search` from a single browser whenever filters changed during an in-flight pagination call.

The container was marked `unhealthy` immediately, but stayed `Up`. Gunicorn auto-booted replacement workers, each of which crashed on the next `/search` call. The `matches`, `map`, and `discover` pages in the PWA returned 502 or hung.

**No signal reached anyone for approximately 11 hours.**

The root SQL race has since been fixed by `ahavah-api` commit `23db093` — a `pg_advisory_xact_lock` per `searcher_person_id` at the head of the search transaction. Same pattern as `service/tokens/__init__.py:debit()` (token-spend race). Different users still run `/search` in parallel; same-user concurrent calls serialize.

This work is orthogonal to the SQL fix. It closes the observability gap that made the incident "11 hours of users seeing broken pages" instead of "2 minutes of human-detected then-fixed problem." Without it, the next class-of-incident (whatever it turns out to be) will fail the same way.

## What the plan adds

| Layer | What | Detects what |
|---|---|---|
| External — UptimeRobot | HTTP keyword monitor on `https://api.ahavah.app/health`, 60s interval, email + SMS on 2 consecutive failures | Whole-droplet outage, api process dead, network partition |
| External — Sentry | `sentry-sdk[flask]` capturing every uncaught exception, email per new issue | Unhandled exceptions, deployments that introduce new error classes |
| In-droplet — `willfarrell/autoheal` sidecar | Restarts any container with the `ahavah` label that has been unhealthy >2 min | Wedged containers (the exact 2026-06-08 case) |
| In-api — `system_error_log` table | One row per 5xx written by Flask error handler. 30-day retention. | Errors visible in admin dashboard without an external Sentry trip |
| In-api — `cron_run_log` table | Row per cron tick, status transitions `running` → `success` / `error`. 30-day retention. | Cron crashes, runaway cron runtimes, stuck `'running'` rows that indicate the api died mid-tick |
| Admin endpoints | 5 new `/admin/system/*` endpoints (existing `/health` extended + 4 new) | The dashboard's data path |
| Admin tiles | 4 new tiles + extended Health on Screen 9 (System tab) | Single pane of glass for the whole stack |

Three principles encoded in the design:

1. **Detection always happens off-droplet.** UptimeRobot pings from outside; Sentry's SDK queues errors locally and ships on next process startup. Both can fire when the droplet is degraded.
2. **Recovery happens on-droplet, autonomously.** autoheal restarts unhealthy containers without a human in the loop.
3. **Visibility funnels into one screen.** The admin dashboard reads from external (UptimeRobot REST, Sentry REST) and internal (`system_error_log`, `cron_run_log`) sources via 5 endpoints. No context-switching during an incident.

## Where things slot into the existing codebase

### Backend (`d:/Antigravity/ahavah-api`)

The plan deliberately follows existing conventions:

- **Migrations** — `migrations/00NN_name.sql`, wrapped in `BEGIN; ... COMMIT;`, idempotent via `IF NOT EXISTS`. Numbered sequentially after `0026_admin_audit_log.sql`. New: `0027_system_error_log.sql`, `0028_cron_run_log.sql`.
- **Admin queries** — `service/admin/queries/<topic>.py`, re-exported from `service/admin/queries/__init__.py`. New: `system_observability.py`.
- **Admin routes** — `service/api/admin/<topic>_routes.py`, registered by `import` at the bottom of `service/api/__init__.py` (look for the existing block around line 1052). New: `system_observability_routes.py`.
- **Decorators** — `@aget('/admin/...')` from `service.api.decorators`. The first line of every handler is `require_admin(s)`.
- **Cron jobs** — async `*_once()` function + `*_forever()` driver that sleeps then calls `*_once()` in a loop. Driver registered in `service/cron/__init__.py:main()` via `asyncio.gather`. New: `service/cron/prunesystemlogs/`.
- **Tests** — pytest, `tests/test_*.py`. Fixtures in `tests/conftest.py`: `client` (Flask test client), `db` (rolls back after each test).

The only genuinely new pattern is `service/observability/` as a namespace package. Three modules planned: `__init__.py` (Sentry init + Flask handler), `cron_logged.py` (decorator), `uptimerobot.py` (REST client), `docker_inspect.py` (Docker Engine API client). Each module has one responsibility.

### Frontend (`d:/Antigravity/ahavah-admin`)

The admin app's scaffolding is in place but most tabs are stubs. The plan follows the existing patterns:

- **Types** — `src/lib/types.ts` (one big file). New types appended.
- **React Query hooks** — `src/lib/queries.ts` using `@tanstack/react-query`. New hooks: `useUptime`, `useErrors`, `useContainers`, `useCronStatus`.
- **Tiles** — `src/components/admin/system/tile-*.tsx`. New subdirectory `system/` so future system-tab tiles cluster cleanly.
- **Tab composition** — `src/components/admin/tab-system.tsx` already exists; the four new tiles mount into a 2-column grid above the existing Health card.

The existing System tab (`tab-system.tsx`) already calls `useSystemHealth()` and renders a `TripleStat` over waitlist/beta/photo counts. The plan extends that file by adding a grid of four new tiles above it + surfacing two new fields (disk %, memory %) below.

### Infrastructure

- **`docker-compose.production.yml`** — Add an `autoheal` service. Add `ahavah=true` labels to api/chat/cron/postgres. Confirm each container has a `healthcheck:` directive with a 2-min `start_period`.
- **`.env.production` on droplet** — Append `SENTRY_DSN`, `UPTIMEROBOT_READ_API_KEY`.

## How the existing admin auth works

The new endpoints reuse the existing pattern from `service/api/admin/system_routes.py`:

```python
@aget('/admin/system/whatever')
def get_admin_system_whatever(s: t.SessionInfo):
    require_admin(s)            # 403 if not admin
    # ... query and return JSON
```

`require_admin` lives in `service/admin/__init__.py`. The admin gate keys off person_uuids listed in a settings table; the existing `is_admin(tx, uuid)` query reads that table. No change needed.

On the frontend, the existing `src/lib/api-client.ts` reads `admin.sessionToken` from localStorage and attaches `Authorization: Bearer …` to every call. New hooks just call `api.get('/admin/system/uptime')` etc. and trust the existing wiring.

## How alerts will route once the plan is implemented

```
api /health failing       → UptimeRobot       → email + SMS    ~2 min
container unhealthy       → autoheal          → silent restart ~2 min
                                              → if restart fails, UR pages
unhandled exception       → Sentry SDK        → email per new  <30s
                                                issue (deduped)
slow query > 1s           → Postgres log      → dashboard read
cron job crashed          → cron decorator    → dashboard read
                                                (RED pill on Cron tile)
```

Deliberate non-alerts: 4xx responses, individual cron failures, slow queries. They surface via the dashboard, no per-event paging. This keeps alert volume at "every page is one a human should actually look at."

## How to verify after implementation

End-to-end smoke a fresh developer can run after Phase 5 lands:

```bash
# 1. Force a 500 (any endpoint that's known to throw on bad input)
curl -sk -X POST https://api.ahavah.app/decisions \
  -H 'Authorization: Bearer notvalid' \
  -H 'Content-Type: application/json' \
  -d '{}'

# 2. Wait 30s. Expect:
#    - email from Sentry titled "New Alert: ..."
#    - new row in system_error_log
#    - new row visible in admin.ahavah.app System tab > Errors tile

# 3. Stop the api container for 3 minutes:
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 'docker stop ahavah-api-api-1'

# 4. Within ~2 min expect:
#    - email + SMS from UptimeRobot "DOWN"
#    - autoheal log: "Container Failed health check"
#    - api container "Restarting (0)"

# 5. autoheal restarts it; UR sends recovery email
```

If any of 2 / 4 fail, the troubleshooting table at the bottom of the plan's Phase 6 Task 6.1 has the likely causes mapped to fixes.

## Where the secrets live (after Phase 0)

- `SENTRY_DSN` — `/opt/ahavah-api/.env.production` on droplet, surfaced into the api container via `docker-compose.production.yml` `environment:`
- `UPTIMEROBOT_READ_API_KEY` — same file, same mechanism
- `AHAVAH_RELEASE_SHA` — set per-deploy, lets Sentry group issues by release

For SSH access to the droplet: `~/.ssh/id_ed25519_ahavah` → `root@167.71.93.27`.

## Known operational quirks the new developer will hit

- **Docker compose env loading is fragile.** Every `docker compose ... up` requires `set -a && source .env.production && set +a` first because the compose file uses `${VAR}` substitution from shell env, not `env_file:`. This is documented in past commit messages and is not new to this work.
- **Health check `start_period` matters.** The api container takes ~30-60s to come up (gunicorn workers, FireHOL IP blocklist download). If `start_period: 30s` it'll be marked unhealthy on first boot before it's actually unhealthy. The plan specifies `start_period: 120s` to avoid this.
- **The docker socket mount is read-only on api.** `/var/run/docker.sock:/var/run/docker.sock:ro`. The api container reads container state but can't restart anything — that's autoheal's job.
- **`asyncio.gather` ordering matters slightly.** Crons with shorter intervals end up running more frequently and their decorator-rows accumulate faster. The `cron_run_log` indexed on `(cron_name, started_at DESC)` so high-volume crons don't slow the dashboard's read query.

## What's deliberately out of scope

See the spec's §3 "Non-goals." Notably:

- No public status page for end users.
- No Sentry performance traces (errors only, `traces_sample_rate=0.0`).
- No log aggregation off the droplet (Loki, Vector, etc.). gunicorn stdout stays on the box.
- No distributed tracing — single droplet, single Postgres, doesn't need it.
- No anomaly detection / ML — manual eyeball via dashboard is fine at current scale.
- No multi-region failover.
- No per-cron alerting (dashboard read only, no email per cron failure).

Push back on adding any of these without a clear use case — each is its own can of worms.

## Session context (broader background the new developer may want)

This work was scoped by an AI agent on 2026-06-09 after a user (`admin@techbaseltd.com`) discovered the 2026-06-08 outage and asked for a plan that would close the observability gap before launch. The conversation that produced the spec and plan included the karpathy-guidelines and superpowers:brainstorming + superpowers:writing-plans skills.

Key context that may not be obvious from the codebase alone:

- **The app is not yet open to the public.** Only two real persons (harrigan + shemele) plus one fresh tester (ochemejohn0) and the user themselves exist in the `person` table. Beta cohort of 27 sits in `beta_signup` with `reengagement_sent_at` recently populated for several.
- **`signup.ahavah.app` is the bypass URL** that lets invitees onboard before launch; `ahavah.app/*` redirects `/auth/*` to `/waitlist` via `src/proxy.ts` middleware (PRELAUNCH gate). On 2026-06-09 morning the gate was bypassed for invited users via the `ahavah.authed` cookie path.
- **`AHAVAH_SIGNUPS_OPEN=true`** as of 2026-06-09 — any email domain can sign up. Was `false` previously (only allowed-domains list could sign up).
- **Vercel deploys auto-fire** from `master` on `ahavah-web` and `ahavah-admin`. There's a GHA mirror workflow that pushes `ahavah-web/master` into the `ahavah-signup` Vercel project (which serves `signup.ahavah.app`). Backend `ahavah-api` requires manual SSH + rebuild on the droplet.
- **The Stripe integration is in test mode.** `STRIPE_SECRET_KEY` starts with `sk_test_`. Real cards entered at checkout are rejected. Live cutover requires creating live-mode Price IDs and rotating the env vars.

If the new developer is picking up cold and needs more context: read the existing handover docs in `docs/superpowers/handovers/`, especially `2026-06-06-beta-referrals-handover.md` for the referrals flow, and the admin dashboard spec at `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md` for the broader dashboard plan.

## Troubleshooting (after implementation)

| Symptom | Likely cause | Fix |
|---|---|---|
| Sentry inbox empty after forced 500 | `SENTRY_DSN` not in api container env | `docker exec ahavah-api-api-1 env \| grep SENTRY_DSN`; if missing, redeploy with the var set |
| `system_error_log` empty after forced 500 | error handler not wired | check `service/api/__init__.py` calls `install_error_handler(app)` |
| Uptime tile shows "data unavailable" | UR API key missing or rate-limited | `docker exec ahavah-api-api-1 env \| grep UPTIMEROBOT`; check UR account quota |
| Containers tile shows "data unavailable" | docker socket not mounted on api | check `docker-compose.production.yml` api service has `/var/run/docker.sock` mount |
| Cron tile shows no rows | `@cron_logged` not applied to any cron | `grep '@cron_logged' service/cron/` should return 12+ matches |
| autoheal not restarting | label mismatch, or `start_period` too long | `docker logs autoheal`; verify each ahavah container has `ahavah=true` label and a 2-min `start_period` |

## References

- Spec: `docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md`
- Plan: `docs/superpowers/plans/2026-06-09-observability-and-admin-monitoring-implementation.md`
- Admin screens spec (Screen 9 = System tab): `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md`
- Admin implementation plan: `docs/superpowers/plans/2026-06-06-admin-dashboard-implementation.md`
- Race fix that motivated this work: `ahavah-api` commit `23db093` in `service/search/__init__.py`
- UptimeRobot docs: https://uptimerobot.com/api/
- Sentry Python SDK: https://docs.sentry.io/platforms/python/integrations/flask/
- willfarrell/autoheal: https://github.com/willfarrell/docker-autoheal

## Post-implementation: update this doc

After Phase 5 lands, update this handover's status line at the top from "DESIGN + PLAN COMPLETE, IMPLEMENTATION NOT YET STARTED" to "LIVE since YYYY-MM-DD." If anything in the spec ended up differing from what landed, note the deltas in a new "What actually landed" section. Plan Task 6.1 references the same handover path — that task should become "update this file" not "write it" if the new developer is reading this.
