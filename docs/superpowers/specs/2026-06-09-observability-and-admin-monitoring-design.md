# Observability and Admin Monitoring — Design Spec

**Date:** 2026-06-09
**Status:** Draft for implementation
**Author:** Generated via brainstorming skill with user
**Implements:** Closes the observability gap that turned a search-cache SQL race into an 11-hour-undetected outage on 2026-06-08.

---

## 1. Project context (read first)

On 2026-06-08 a gunicorn worker in `ahavah-api` SIGSEGV'd on a `psycopg.errors.UniqueViolation` raised by the `Q_UNCACHED_SEARCH_2` INSERT into `search_cache`. The race was triggered by a frontend change (`ahavah-web` commit `7a2a65d`) that made same-user concurrent `/search` calls routine. The container was marked `unhealthy` but stayed `Up`; the matches / map / discover surfaces in the PWA returned 502 or hung; no signal reached anyone for ~11 hours.

The root SQL race has been fixed by `23db093` (per-user advisory lock in the search transaction). This spec covers the orthogonal failure: **nothing detected the outage, nothing alerted anyone, and nothing tried to restart the unhealthy container.**

Existing surfaces this design extends:

- **ahavah-api admin backend** at `service/api/admin/` — 8 modules already exist including `system_routes.py` (`GET /admin/system/health`) and `overview_routes.py` (`/admin/whoami`, `/admin/overview`). The `require_admin` / `is_admin` gate is in place.
- **ahavah-admin frontend** at `src/app/` — only `auth/sign-in`, landing, and `unauthorized` exist. The screens spec (`docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md`) defines a 6-tab nav including a **System tab (Screen 9)**.
- **Postgres** — single instance on the droplet, schema reachable via `database.api_tx`.

This design touches all three.

## 2. Goal

Turn an outage of any kind (worker crash, container wedge, segfault, database failure, runaway exception) from "discovered hours later when a user complains" into "detected within 2 minutes, optionally auto-recovered, surfaced in the admin dashboard before a human gets to it."

## 3. Non-goals (out of scope)

- Public status page for end users.
- Sentry performance traces (errors only, `traces_sample_rate=0.0`).
- Log aggregation off the droplet (Loki, Vector, etc.).
- Distributed tracing across services.
- Anomaly detection / ML.
- Multi-region failover.
- Per-cron alerting (dashboard read only).

## 4. Architecture

```
                        ┌─────────────────────────────────────┐
                        │  EXTERNAL (survives droplet outage) │
                        ├─────────────────────────────────────┤
                        │  UptimeRobot  ──┐                   │
                        │  ping /health   │                   │
                        │  every 60s      │ on 2 fails:       │
                        │                 ├─ email + SMS      │
                        │                 │   to admin        │
                        │  Sentry.io   ───┤                   │
                        │  exception      │                   │
                        │  capture        │                   │
                        └─────────────────┼───────────────────┘
                                          │  status / errors
                                          │  pulled via REST
                  ┌───────────────────────▼────────────────────┐
                  │           DROPLET                           │
                  │   ┌─────────────────────────────────────┐  │
                  │   │  autoheal sidecar (new container)   │  │
                  │   │  restarts api / chat / cron if      │  │
                  │   │  health-check unhealthy >2 min      │  │
                  │   └─────────────────────────────────────┘  │
                  │                                             │
                  │   ┌────────────────────────────────────┐    │
                  │   │  ahavah-api (existing)             │    │
                  │   │  + Flask error middleware writes   │    │
                  │   │    each 5xx to:                    │    │
                  │   │      • Sentry (network)            │    │
                  │   │      • system_error_log (postgres) │    │
                  │   │  + cron decorator writes each      │    │
                  │   │    tick start/end to cron_run_log  │    │
                  │   │  + 5 new admin endpoints           │    │
                  │   └────────────────────────────────────┘    │
                  │                  │                          │
                  │   ┌──────────────▼──────────────────────┐   │
                  │   │  postgres                            │   │
                  │   │  + system_error_log (new)            │   │
                  │   │  + cron_run_log (new)                │   │
                  │   │  daily prune job: 30d retention      │   │
                  │   └──────────────────────────────────────┘   │
                  └─────────────────────────────────────────────┘
                                          │
                                          │  /admin/system/*
                                          │
                  ┌───────────────────────▼────────────────────┐
                  │  ahavah-admin (existing) — System tab adds: │
                  │  Uptime · Errors · Containers · Cron Status │
                  │  + extends existing Health snapshot tile    │
                  └─────────────────────────────────────────────┘
```

Three principles:

1. **Detection always happens off-droplet.** UptimeRobot can see `/health` is down even when the droplet is dead. Sentry's SDK queues locally and flushes on next process startup, so a dying worker can still report its cause.
2. **Recovery happens on-droplet, autonomously.** autoheal restarts unhealthy containers without a human in the loop.
3. **Visibility funnels back into one screen.** The admin dashboard reads from both external (UptimeRobot REST, Sentry REST) and internal (`system_error_log`, `cron_run_log`) sources. One System tab, no context-switching during an incident.

## 5. Components

### C1 — UptimeRobot monitor

- 1 monitor on `https://api.ahavah.app/health`, HTTP keyword check for `status: ok`, 60s interval.
- Alert contacts: email to `admin@techbaseltd.com` and SMS to the admin's mobile.
- Alert trigger: 2 consecutive failures (avoids paging on a single transient blip) → ~2 min detection.
- One-time external setup: ~10 min. No code in this repo, no recurring cost (free tier covers 50 monitors).
- Operational note: the UptimeRobot REST API key is stored as `UPTIMEROBOT_READ_API_KEY` in `.env.production` for the dashboard tile (C6) to read uptime history.

### C2 — Sentry SDK in `ahavah-api`

- Add `sentry-sdk[flask]` to `requirements.txt`.
- Init in `service/api/__init__.py` keyed off `SENTRY_DSN` env var. If unset, the SDK is a no-op — dev / test runs are unaffected:
    ```python
    if os.environ.get("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.environ["SENTRY_DSN"],
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            environment=os.environ.get("DUO_ENV", "prod"),
            release=os.environ.get("AHAVAH_RELEASE_SHA"),
        )
    ```
- Captures unhandled exceptions automatically. Flask integration adds request URL + person_uuid from session info.
- Sentry's free tier covers 5k errors/mo, which fits.
- The same exception is also written to `system_error_log` (C3). Sentry is for instant external alerts; the local table powers the dashboard Errors tile without an external API call on every dashboard load.

### C3 — `system_error_log` table + Flask error handler

- Migration `0035_system_error_log.sql`:
    ```sql
    CREATE TABLE system_error_log (
        id              BIGSERIAL PRIMARY KEY,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        path            TEXT NOT NULL,
        method          TEXT NOT NULL,
        status_code     SMALLINT NOT NULL,
        exception_class TEXT NOT NULL,
        traceback       TEXT NOT NULL,
        person_id       INTEGER REFERENCES person(id) ON DELETE SET NULL,
        request_id      TEXT
    );
    CREATE INDEX idx_system_error_log_recent
        ON system_error_log (created_at DESC, status_code);
    ```
- Flask handler:
    ```python
    @app.errorhandler(Exception)
    def log_unhandled(exc):
        tb = traceback.format_exc()
        with api_tx() as tx:
            tx.execute(
                """INSERT INTO system_error_log
                       (path, method, status_code, exception_class,
                        traceback, person_id, request_id)
                   VALUES (%(path)s, %(method)s, 500, %(klass)s,
                           %(tb)s, %(pid)s, %(rid)s)""",
                dict(path=request.path, method=request.method,
                     klass=type(exc).__name__, tb=tb,
                     pid=getattr(g, "person_id", None),
                     rid=request.headers.get("X-Request-ID")),
            )
        raise  # let Sentry + Flask's default 500 still fire
    ```
- Only 5xx are logged (Flask's default 4xx — auth failures, validators — are too noisy and expected).
- `traceback` is the full Python traceback string. The dashboard renders it as-is in `<pre>`; no per-frame schema.
- `request_id` is `request.headers.get('X-Request-ID')` if a proxy sets it, else `None`.
- `person_id ON DELETE SET NULL` so deleting a user doesn't cascade-wipe their error history.

### C4 — `cron_run_log` table + `@cron_logged` decorator

- Migration `0036_cron_run_log.sql`:
    ```sql
    CREATE TABLE cron_run_log (
        id            BIGSERIAL PRIMARY KEY,
        cron_name     TEXT NOT NULL,
        started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at   TIMESTAMPTZ,
        status        TEXT NOT NULL
                      CHECK (status IN ('running', 'success', 'error')),
        error_message TEXT,
        rows_affected INTEGER
    );
    CREATE INDEX idx_cron_run_log_recent_per_cron
        ON cron_run_log (cron_name, started_at DESC);
    ```
- Decorator in `service/cron/cronutil/__init__.py`:
    ```python
    def cron_logged(name: str):
        def deco(fn):
            async def wrapped(*args, **kw):
                async with asyncdatabase.api_tx() as tx:
                    cur = await tx.execute(
                        """INSERT INTO cron_run_log (cron_name, status)
                           VALUES (%(n)s, 'running') RETURNING id""",
                        dict(n=name),
                    )
                    run_id = (await cur.fetchone())["id"]
                try:
                    result = await fn(*args, **kw)
                    async with asyncdatabase.api_tx() as tx:
                        await tx.execute(
                            """UPDATE cron_run_log SET status='success',
                                       finished_at=NOW(), rows_affected=%(r)s
                               WHERE id=%(id)s""",
                            dict(id=run_id, r=getattr(result, "rows", None)),
                        )
                    return result
                except Exception as e:
                    async with asyncdatabase.api_tx() as tx:
                        await tx.execute(
                            """UPDATE cron_run_log SET status='error',
                                       finished_at=NOW(), error_message=%(m)s
                               WHERE id=%(id)s""",
                            dict(id=run_id, m=str(e)[:1000]),
                        )
                    raise
            return wrapped
        return deco
    ```
- Apply to every existing `*_once()` cron function. Audit during implementation: `betareengagement`, `nsfwphotorunner`, `referralintro`, `expireboosts`, `expiredaypass`, any others found.
- If the api crashes mid-cron, the row stays `'running'` forever. The dashboard tile flags any `'running'` row older than 1 hour as `'crashed'` on the read side. No background reaper needed.

### C5 — `/admin/system/containers` endpoint

- New route in `service/api/admin/system_routes.py`.
- Approach A (preferred): give the api container access to `/var/run/docker.sock` via a read-only mount, query Docker Engine HTTP API directly. Returns `[{name, status, health_status, uptime_seconds, image_id}]`.
- Approach B (fallback): if mounting the docker socket is undesirable for security, autoheal exposes container state on its own port; this endpoint proxies that.
- Tile polls every 10s from the FE; no live stream.
- Decision on A vs B is deferred to the implementation plan; both are equally simple.

### C6 — `/admin/system/uptime` endpoint

- Pulls from UptimeRobot REST API using `UPTIMEROBOT_READ_API_KEY` from env.
- Returns:
    ```json
    {
      "current": "up" | "down" | "paused",
      "uptime_24h": 99.97,
      "uptime_7d":  99.95,
      "uptime_30d": 99.92,
      "events": [
        {"type": "down", "at": "2026-06-08T23:01:00Z", "duration_s": 39600}
      ]
    }
    ```
- Cached server-side for 30s using `functools.lru_cache` keyed on a 30s time bucket — keeps usage under UptimeRobot's rate limit.
- API key never reaches the browser.

### C7 — `/admin/system/errors` + `/admin/system/crons` endpoints

- `/admin/system/errors?since=24h|7d|30d` (default `24h`):
    ```sql
    SELECT id, created_at, path, method, status_code, exception_class,
           traceback, person_id
      FROM system_error_log
     WHERE created_at > NOW() - INTERVAL %(since)s
     ORDER BY created_at DESC
     LIMIT 50
    ```
- `/admin/system/crons`:
    ```sql
    WITH latest AS (
        SELECT DISTINCT ON (cron_name)
               cron_name, status, started_at, finished_at, error_message
          FROM cron_run_log
         ORDER BY cron_name, started_at DESC
    ),
    rates_24h AS (
        SELECT cron_name,
               COUNT(*) FILTER (WHERE status='success')::float
                 / NULLIF(COUNT(*), 0) AS success_rate,
               MAX(finished_at - started_at) AS longest_run
          FROM cron_run_log
         WHERE started_at > NOW() - INTERVAL '24 hours'
         GROUP BY cron_name
    )
    SELECT l.*, r.success_rate, r.longest_run
      FROM latest l LEFT JOIN rates_24h r USING (cron_name)
     ORDER BY cron_name
    ```
- Both gated by `require_admin`.

### C8 — autoheal sidecar

Add to `docker-compose.production.yml`:

```yaml
autoheal:
  image: willfarrell/autoheal:1.2.0
  container_name: autoheal
  restart: always
  environment:
    - AUTOHEAL_CONTAINER_LABEL=ahavah
    - AUTOHEAL_DEFAULT_STOP_TIMEOUT=10
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
```

Add `labels: [ahavah]` (or equivalent label key autoheal expects) to `api`, `chat`, `cron`, `postgres` services so autoheal scopes to our stack only. Each container's `healthcheck:` directive determines what "unhealthy" means. Confirm api's `healthcheck` has a 2-min `start_period` so a slow startup doesn't trigger immediate restart.

### C9 — Admin dashboard tiles (System tab)

Add five tiles to Screen 9. Use the existing System-tab shell, the existing skeleton/empty/error patterns from the screens spec, and the existing primitive set.

| Tile | Data source | Refresh | Visual |
|---|---|---|---|
| **Uptime** | `/admin/system/uptime` | 30s | Status pill (Up / Down), 24h / 7d / 30d % uptime, sparkline of last 24h |
| **Errors** | `/admin/system/errors` | 30s | Latest 10 5xx with relative timestamp, status code, path; click row → expand traceback |
| **Containers** | `/admin/system/containers` | 10s | 5 rows (api/chat/cron/postgres/autoheal), each with health pill + uptime |
| **Cron Status** | `/admin/system/crons` | 30s | 1 row per cron, last-run relative time, success/error pill, expand for last 10 runs |
| **Health (extend)** | `/admin/system/health` | 30s | Keeps existing DB health + OTP-24h; adds disk and memory usage |

## 6. Data model

(Full DDL given inline in C3 and C4.)

**Retention:** daily prune cron `prune_system_logs` runs at 03:00 UTC:

```sql
DELETE FROM system_error_log WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM cron_run_log     WHERE created_at < NOW() - INTERVAL '30 days';
```

**Bounded growth:** at the peak of an incident burst, ~1k errors/hour is plausible. 30 days × 24k = 720k rows max. Postgres handles that trivially with the indexes above.

## 7. Alert routing

| Incident type | Detector | Alert channel | Time-to-alert |
|---|---|---|---|
| api `/health` failing | UptimeRobot | email + SMS | ~2 min |
| container unhealthy | autoheal | silent restart; if restart fails, `/health` keeps failing → UptimeRobot pages | 2 min restart attempt |
| Unhandled exception | Sentry SDK | email per new issue (deduped by stack) | <30s |
| Slow query >1s | Postgres log | logged only, surface via dashboard read | — |
| Cron job crashed | cron decorator | logged only, dashboard tile shows red pill | — |

Two alert channels exist (UptimeRobot for liveness, Sentry for correctness). Both ping the same human; neither requires the droplet to be alive to fire.

Deliberately NOT alerting on:
- 4xx responses (auth failures, validators).
- Individual cron failures.
- Slow queries (read-only surface).

This keeps alert volume at "every page is one a human should look at," which avoids the dashboard becoming background noise.

## 8. Phasing

Five phases, each independently shippable.

**Phase 0 — External setup (no code)** — ~30 min
- Register UptimeRobot account, add monitor on `/health`.
- Register Sentry.io account, get DSN.
- Store secrets in droplet `.env.production`: `SENTRY_DSN`, `UPTIMEROBOT_READ_API_KEY`.
- Verify monitor fires by stopping api briefly.
- **Acceptance:** receive a real email + SMS when `/health` goes down.

**Phase 1 — Sentry SDK + `system_error_log` table (backend)** — ~2 hr
- Migration `0035`.
- `sentry_sdk` init in `service/api/__init__.py`.
- Flask error middleware writes to both Sentry and `system_error_log`.
- Manual test: trigger a 500 and verify both surfaces capture it.
- **Acceptance:** forced exception appears in Sentry within 30s AND shows up in `SELECT * FROM system_error_log`.

**Phase 2 — `cron_run_log` + decorator (backend)** — ~2 hr
- Migration `0036`.
- `@cron_logged` decorator in `cronutil`.
- Apply to all existing cron `*_once()` functions (audit list during implementation).
- `prune_system_logs` cron added.
- **Acceptance:** `SELECT * FROM cron_run_log` shows one row per tick with status transitioning `running` → `success`.

**Phase 3 — Admin endpoints (backend)** — ~3 hr
- `/admin/system/uptime` (UptimeRobot proxy with 30s cache).
- `/admin/system/errors` (read).
- `/admin/system/crons` (read with derived metrics).
- `/admin/system/containers` (docker socket or autoheal proxy; decision pinned in plan).
- `/admin/system/health` extended (add disk + memory).
- **Acceptance:** all five endpoints return JSON under `require_admin`, 200 for admin, 403 for others.

**Phase 4 — Admin dashboard tiles (FE)** — ~4 hr
- 4 new tiles + 1 extended tile on System tab.
- Skeleton / empty / error states per existing patterns.
- Tile-level refresh intervals per the table in C9.
- **Acceptance:** open `admin.ahavah.app/system`, see all 5 tiles load with real data, error states render when the underlying API call 5xxs.

**Phase 5 — autoheal sidecar (infra)** — ~1 hr
- Add autoheal service to `docker-compose.production.yml`.
- Add label to api / chat / cron / postgres.
- Verify each container's `healthcheck:` directive.
- Test: kill api worker manually, verify autoheal restarts within 2 min.
- **Acceptance:** forcing api into unhealthy state for 2+ min causes autoheal to restart it without intervention.

**Total:** ~13 hours of work across 5 phases. Each phase ships value on its own and unblocks the next.

## 9. Open decisions deferred to the implementation plan

- Docker socket mount vs autoheal-proxy approach for C5.
- Exact Postgres healthcheck behavior under load (whether to add a query-based check beyond the existing pg_isready).
- Per-cron `rows_affected` reporting: which crons should report, which should leave NULL.
- Whether to add an `audit_log` cross-reference column on `system_error_log` for actions taken inside an admin request that errored (likely unnecessary at v1 but flagged).

## 10. References

- Screens spec: `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md` (Screen 9 System tab.)
- Admin dashboard implementation plan: `docs/superpowers/plans/2026-06-06-admin-dashboard-implementation.md`
- Existing admin backend: `service/api/admin/{system,overview,users,users_action,cohorts,economy,moderation,audit}_routes.py`
- Existing admin queries: `service/admin/queries.py`
- Search race fix (the incident that motivated this): `service/search/__init__.py` commit `23db093`.
