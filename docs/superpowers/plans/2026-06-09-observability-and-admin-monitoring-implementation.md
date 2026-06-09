# Observability and Admin Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add observability and admin monitoring to `ahavah-api` and `ahavah-admin` so any future outage (worker crash, container wedge, segfault, database failure, unhandled exception, cron failure) is detected within 2 minutes — page-via-email/SMS, autonomous container restart, and full visibility in the admin System tab — instead of sitting undiagnosed for hours like the 2026-06-08 `search_cache` UniqueViolation did.

**Architecture:** Three external SaaS surfaces (UptimeRobot for liveness probing, Sentry.io for exception capture, `willfarrell/autoheal` Docker sidecar for in-droplet recovery) cover the "must work when the api is dead" cases. Two new Postgres tables (`system_error_log`, `cron_run_log`) cover the "must work when the api is alive" cases. Five new admin endpoints under `/admin/system/*` and five new tiles on the existing System tab funnel everything into one dashboard view.

**Tech Stack:** Python 3.11 (Flask + gunicorn + psycopg + sentry-sdk[flask]), PostgreSQL 16, Docker Compose, Next.js 16 / React 19 / Tailwind v4 with `@tanstack/react-query` (ahavah-admin), TypeScript, UptimeRobot REST API, Sentry SaaS, `willfarrell/autoheal:1.2.0`.

**Spec:** `docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md`

**Phase order:** 0 → 5 sequentially. Phases 1, 2 and 5 are independently shippable. Phases 3 and 4 must ship together for the dashboard tiles to render data.

---

## File Structure

### `ahavah-api` (backend repo at `d:/Antigravity/ahavah-api`)

**New files:**

| Path | Responsibility |
|---|---|
| `migrations/0027_system_error_log.sql` | Schema: `system_error_log` table + indexes. Idempotent. |
| `migrations/0028_cron_run_log.sql` | Schema: `cron_run_log` table + indexes. Idempotent. |
| `service/admin/queries/system_observability.py` | All read queries used by the new admin endpoints. |
| `service/api/admin/system_observability_routes.py` | The 4 new admin endpoints (`/admin/system/errors`, `/crons`, `/uptime`, `/containers`). |
| `service/cron/prunesystemlogs/__init__.py` | Daily cron that prunes both new log tables to 30 days. |
| `service/observability/__init__.py` | Sentry init + Flask error handler that writes to `system_error_log`. |
| `service/observability/cron_logged.py` | `@cron_logged(name)` decorator that writes start/end rows to `cron_run_log`. |
| `service/observability/uptimerobot.py` | UptimeRobot REST client + 30s server-side cache for the `/admin/system/uptime` proxy. |
| `service/observability/docker_inspect.py` | Reads container state from the Docker Engine API via the mounted socket. |
| `tests/test_observability_init.py` | Pytest: Sentry init no-ops without DSN, fires with DSN. |
| `tests/test_system_error_log.py` | Pytest: error handler writes a row per 5xx. |
| `tests/test_cron_logged.py` | Pytest: decorator writes running → success / error transitions. |
| `tests/test_admin_system_observability_routes.py` | Pytest: each new endpoint imports + has shape. |

**Modified files:**

| Path | Change |
|---|---|
| `requirements.txt` | Add `sentry-sdk[flask]==2.20.0`. |
| `service/api/__init__.py` | Call `service.observability.init_sentry(app)` after `app = Flask(...)`; register error handler; import new admin routes module. |
| `service/admin/queries/__init__.py` | Re-export the new query constants from `system_observability.py`. |
| `service/cron/__init__.py` | Import `prune_system_logs_forever` and add to `asyncio.gather` list. Wrap existing `*_forever` modules' `*_once` functions with `@cron_logged`. |
| `docker-compose.production.yml` | Add `autoheal` service. Add `labels: ahavah` to api/chat/cron/postgres. Verify each container's `healthcheck:` has 2-min `start_period`. |
| `.env.production` (droplet) | Add `SENTRY_DSN=…`, `UPTIMEROBOT_READ_API_KEY=…`. |
| `docs/superpowers/handovers/2026-06-09-observability-handover.md` | New handover doc (created at end of plan). |

### `ahavah-admin` (frontend repo at `d:/Antigravity/ahavah-admin`)

**New files:**

| Path | Responsibility |
|---|---|
| `src/components/admin/system/tile-uptime.tsx` | Uptime tile (24h / 7d / 30d, sparkline). |
| `src/components/admin/system/tile-errors.tsx` | Errors feed (latest 10, expandable traceback). |
| `src/components/admin/system/tile-containers.tsx` | Container health rows (5 services). |
| `src/components/admin/system/tile-cron-status.tsx` | Cron summary (last-run pill + success rate). |

**Modified files:**

| Path | Change |
|---|---|
| `src/lib/types.ts` | Add `UptimeResponse`, `ErrorsResponse`, `ContainersResponse`, `CronStatusResponse` types. |
| `src/lib/queries.ts` | Add `useUptime`, `useErrors`, `useContainers`, `useCronStatus` React Query hooks. |
| `src/components/admin/tab-system.tsx` | Mount the four new tiles + extend the existing Health tile to surface disk/memory. |

---

## Phase 0 — External SaaS setup (no code)

**Goal:** Two SaaS accounts wired up, both verified end-to-end by triggering an alert.

### Task 0.1: Register UptimeRobot monitor

**Files:** None (external SaaS).

- [ ] **Step 1: Create UptimeRobot account**

Go to `https://uptimerobot.com/`, sign up with `admin@techbaseltd.com`. Confirm the email.

- [ ] **Step 2: Add HTTP keyword monitor**

In the dashboard, click "+ New Monitor". Settings:
- Monitor Type: `HTTP(s) - Keyword`
- Friendly Name: `ahavah-api /health`
- URL: `https://api.ahavah.app/health`
- Keyword Type: `exists`
- Keyword Value: `status: ok`
- Monitoring Interval: `1 minute` (or `5 minutes` on free tier; `1 minute` is paid)
- Monitor Timeout: `30 seconds`

Click "Create Monitor".

- [ ] **Step 3: Add alert contacts**

Settings → My Settings → Alert Contacts. Add:
- Type: `Email`, value `admin@techbaseltd.com`
- Type: `SMS`, value the admin's mobile (e.g. `+1246xxxxxxx`)

Attach both contacts to the monitor (Edit monitor → "Alert Contacts To Notify").

- [ ] **Step 4: Capture the read-only API key**

Settings → API Settings → "Read-Only API Key" → "Create". Copy the key (it starts with `ur` followed by 32 hex chars). Set it aside for Phase 0 task 0.3.

- [ ] **Step 5: Verify alert fires**

Stop the api container momentarily so /health returns no response:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 'docker stop ahavah-api-api-1'
```

Wait 3 minutes (1 min interval × 2 fails + grace). Confirm an email + SMS arrive saying the monitor is DOWN.

Restart it:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 'docker start ahavah-api-api-1'
```

Wait another 2 minutes. Confirm a recovery email arrives.

**Acceptance:** real email + SMS received on both DOWN and UP transitions.

### Task 0.2: Register Sentry project

**Files:** None (external SaaS).

- [ ] **Step 1: Create Sentry account**

Go to `https://sentry.io/signup/`. Sign up with `admin@techbaseltd.com`. Choose plan: `Developer (free)`. Organization name: `ahavah`.

- [ ] **Step 2: Create project**

Click "Create Project". Platform: `Python` → `Flask`. Project name: `ahavah-api`. Alert frequency: `Alert me on every new issue`.

- [ ] **Step 3: Copy DSN**

Settings → Projects → ahavah-api → Client Keys (DSN). Copy the DSN string (looks like `https://abc123@o12345.ingest.sentry.io/67890`). Set it aside for task 0.3.

- [ ] **Step 4: Configure alert routing**

Settings → Projects → ahavah-api → Alerts → "Create Alert Rule":
- Name: `New issue created`
- When: `An issue is first seen`
- Then: `Send a notification to Email` → `admin@techbaseltd.com`

Save.

**Acceptance:** Project page accessible, DSN captured.

### Task 0.3: Store secrets on droplet

**Files:**
- Modify: `/opt/ahavah-api/.env.production` on droplet (167.71.93.27)

- [ ] **Step 1: SSH into droplet**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27
```

- [ ] **Step 2: Append secrets**

```bash
cd /opt/ahavah-api
cp .env.production .env.production.bak.$(date +%Y%m%d-%H%M%S)
printf '\n# Phase 0 — observability\nSENTRY_DSN=%s\nUPTIMEROBOT_READ_API_KEY=%s\n' \
  '<PASTE_SENTRY_DSN_HERE>' \
  '<PASTE_UPTIMEROBOT_API_KEY_HERE>' >> .env.production
```

Replace the two placeholders with the values from 0.1 and 0.2.

- [ ] **Step 3: Verify they read back**

```bash
grep -E '^(SENTRY_DSN|UPTIMEROBOT_READ_API_KEY)=' .env.production
```

Expected: two lines, neither empty after the `=`.

- [ ] **Step 4: Add the env vars to compose so the api container sees them**

Edit `/opt/ahavah-api/docker-compose.production.yml`. Under `services.api.environment:`, add:

```yaml
      SENTRY_DSN: ${SENTRY_DSN}
      UPTIMEROBOT_READ_API_KEY: ${UPTIMEROBOT_READ_API_KEY}
      AHAVAH_RELEASE_SHA: ${AHAVAH_RELEASE_SHA:-unknown}
```

(The third one feeds Sentry's `release` tag so issues group per deploy.)

- [ ] **Step 5: Mirror the same edit in the repo so future deploys keep it**

In `d:/Antigravity/ahavah-api/docker-compose.production.yml`, locate the api service `environment:` block (around the existing `STRIPE_*` lines) and add the same three lines.

- [ ] **Step 6: Commit the repo change**

```bash
cd d:/Antigravity/ahavah-api
git add docker-compose.production.yml
git commit -m "ops(compose): expose SENTRY_DSN + UPTIMEROBOT_READ_API_KEY to api"
git push origin HEAD:ahavah/main
```

**Acceptance:** `docker exec ahavah-api-api-1 env | grep -E 'SENTRY_DSN|UPTIMEROBOT_READ_API_KEY'` returns both vars set (after Phase 1 redeploy).

---

## Phase 1 — Sentry SDK + `system_error_log`

**Goal:** Every unhandled exception in `ahavah-api` lands in BOTH Sentry (instant external alert) and `system_error_log` (queryable for the dashboard tile). Both surfaces capture a forced 500 within 30s.

### Task 1.1: Add `system_error_log` migration

**Files:**
- Create: `d:/Antigravity/ahavah-api/migrations/0027_system_error_log.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- 0027_system_error_log.sql
-- Append-only log of every unhandled exception in ahavah-api. Idempotent.
-- See docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md §C3.
BEGIN;

CREATE TABLE IF NOT EXISTS system_error_log (
  id              BIGSERIAL PRIMARY KEY,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  path            TEXT        NOT NULL,
  method          TEXT        NOT NULL,
  status_code     SMALLINT    NOT NULL,
  exception_class TEXT        NOT NULL,
  traceback       TEXT        NOT NULL,
  person_id       INTEGER     REFERENCES person(id) ON DELETE SET NULL,
  request_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_system_error_log_recent
  ON system_error_log (created_at DESC, status_code);

COMMIT;
```

- [ ] **Step 2: Apply migration on droplet**

```bash
scp -i ~/.ssh/id_ed25519_ahavah d:/Antigravity/ahavah-api/migrations/0027_system_error_log.sql root@167.71.93.27:/tmp/
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker cp /tmp/0027_system_error_log.sql ahavah-api-postgres-1:/tmp/ && \
   docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -f /tmp/0027_system_error_log.sql'
```

Expected output: `BEGIN`, `CREATE TABLE`, `CREATE INDEX`, `COMMIT`.

- [ ] **Step 3: Verify table exists**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c "\d system_error_log"'
```

Expected: schema printed with all 9 columns.

- [ ] **Step 4: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add migrations/0027_system_error_log.sql
git commit -m "feat(observability): add system_error_log table"
```

### Task 1.2: Add `service/observability/__init__.py` with Sentry init and Flask error handler

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/observability/__init__.py`
- Test: `d:/Antigravity/ahavah-api/tests/test_observability_init.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_observability_init.py
"""Tests for the observability module: Sentry init + Flask error handler.

Verified behaviors:
  - init_sentry() is a no-op when SENTRY_DSN is missing (dev / test runs).
  - init_sentry() calls sentry_sdk.init() with the right knobs when DSN is set.
  - install_error_handler() registers a handler that writes one row to
    system_error_log per uncaught exception and re-raises so Sentry still
    sees the same exception.
"""
from __future__ import annotations

import os
from unittest.mock import patch


def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv('SENTRY_DSN', raising=False)
    from service.observability import init_sentry
    with patch('sentry_sdk.init') as mock_init:
        init_sentry()
        mock_init.assert_not_called()


def test_init_sentry_calls_sdk_with_dsn(monkeypatch):
    monkeypatch.setenv('SENTRY_DSN', 'https://abc@o1.ingest.sentry.io/1')
    monkeypatch.setenv('AHAVAH_RELEASE_SHA', 'abc1234')
    from service.observability import init_sentry
    with patch('sentry_sdk.init') as mock_init:
        init_sentry()
        mock_init.assert_called_once()
        kwargs = mock_init.call_args.kwargs
        assert kwargs['dsn'] == 'https://abc@o1.ingest.sentry.io/1'
        assert kwargs['traces_sample_rate'] == 0.0
        assert kwargs['release'] == 'abc1234'
```

- [ ] **Step 2: Run the test, expect it to fail (module doesn't exist)**

```bash
cd d:/Antigravity/ahavah-api
docker exec ahavah-api-api-1 python -m pytest tests/test_observability_init.py -v
```

Expected: `ModuleNotFoundError: No module named 'service.observability'`.

- [ ] **Step 3: Add `sentry-sdk[flask]` to requirements.txt**

Open `d:/Antigravity/ahavah-api/requirements.txt`. Append:

```
sentry-sdk[flask]==2.20.0
```

- [ ] **Step 4: Create `service/observability/__init__.py`**

```python
# service/observability/__init__.py
"""Observability layer: Sentry init + Flask error handler that writes
unhandled exceptions to system_error_log.

Both surfaces capture the same exception:
  - Sentry  → external alert email per new issue (deduped by stack)
  - DB log  → queryable from the admin dashboard Errors tile

The Sentry SDK no-ops when SENTRY_DSN is unset, so dev / test runs and
local pytest don't try to ship telemetry. See spec §C2 and §C3.
"""
from __future__ import annotations

import os
import traceback as tb_module
from typing import Any

import sentry_sdk
from flask import Flask, g, request


def init_sentry() -> None:
    """Initialize Sentry if SENTRY_DSN is set. No-op otherwise."""
    dsn = os.environ.get('SENTRY_DSN')
    if not dsn:
        return
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.0,
        environment=os.environ.get('DUO_ENV', 'prod'),
        release=os.environ.get('AHAVAH_RELEASE_SHA', 'unknown'),
        send_default_pii=False,
    )


_Q_INSERT_ERROR = """
INSERT INTO system_error_log
       (path, method, status_code, exception_class,
        traceback, person_id, request_id)
VALUES (%(path)s, %(method)s, %(status_code)s, %(exception_class)s,
        %(traceback)s, %(person_id)s, %(request_id)s)
"""


def _log_error_row(exc: BaseException, status_code: int) -> None:
    """Best-effort write of one row. Swallow DB errors so the original
    exception still surfaces to Flask + Sentry."""
    from database import api_tx
    try:
        with api_tx() as tx:
            tx.execute(
                _Q_INSERT_ERROR,
                dict(
                    path=request.path,
                    method=request.method,
                    status_code=status_code,
                    exception_class=type(exc).__name__,
                    traceback=tb_module.format_exc(),
                    person_id=getattr(g, 'person_id', None),
                    request_id=request.headers.get('X-Request-ID'),
                ),
            )
    except Exception:
        # Swallow: logging the log failure would cascade. Sentry still
        # sees the original exception via the SDK's Flask integration.
        pass


def install_error_handler(app: Flask) -> None:
    """Register a Flask error handler that writes 5xx exceptions to
    system_error_log AND re-raises so Sentry's FlaskIntegration still
    captures the exception."""

    @app.errorhandler(Exception)
    def handle_uncaught(exc: Exception) -> Any:
        from werkzeug.exceptions import HTTPException
        # Werkzeug HTTPException carries its own status code. Only log 5xx.
        if isinstance(exc, HTTPException):
            if exc.code is not None and 500 <= exc.code < 600:
                _log_error_row(exc, exc.code)
            raise exc
        # Non-HTTP exception → always 500.
        _log_error_row(exc, 500)
        raise exc
```

- [ ] **Step 5: Run the test, expect it to pass**

Rebuild the api container so the new requirement is installed:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'cd /opt/ahavah-api && git pull origin ahavah/main && \
   set -a && source .env.production && set +a && \
   docker compose -f docker-compose.yml -f docker-compose.production.yml build api'
```

(Don't restart yet — the handler isn't wired into the app yet.) Then run the test inside the freshly-built image:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker run --rm --network ahavah-api_default \
     -e DUO_DB_HOST=postgres -e DUO_DB_PORT=5432 \
     -e DUO_DB_USER=postgres -e DUO_DB_PASS=<paste pass> \
     ahavah-api-api:latest python -m pytest tests/test_observability_init.py -v'
```

Expected: 2 passed.

(Note: if the developer doesn't have direct droplet shell access, they should commit, push, redeploy, and run pytest inside the deployed container instead.)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt service/observability/__init__.py tests/test_observability_init.py
git commit -m "feat(observability): Sentry SDK init + system_error_log error handler"
```

### Task 1.3: Wire `init_sentry` + `install_error_handler` into `service/api/__init__.py`

**Files:**
- Modify: `d:/Antigravity/ahavah-api/service/api/__init__.py` (near the Flask app construction)

- [ ] **Step 1: Find the Flask app construction**

Search for `app = Flask` in `service/api/__init__.py`. (Actually the existing code uses `app` from `service.api.decorators`. Find the line `from service.api.decorators import app` near the top.)

- [ ] **Step 2: Insert init + handler registration after the app import**

Add these two lines immediately after the `from service.api.decorators import app` (or wherever `app` first becomes available — confirm the import order is correct):

```python
# Phase 1 — observability: Sentry SDK + system_error_log error handler.
# Sentry no-ops if SENTRY_DSN is unset (dev/test). Error handler always
# registers; logging is best-effort.
from service.observability import init_sentry, install_error_handler  # noqa: E402
init_sentry()
install_error_handler(app)
```

- [ ] **Step 3: Add a smoke test that forces a 500 and verifies a row gets written**

```python
# tests/test_system_error_log.py
"""End-to-end: hitting an endpoint that raises results in exactly one
row in system_error_log + the original exception propagating."""
from __future__ import annotations


def test_error_handler_writes_one_row(client, db, monkeypatch):
    # Register a throw-only test route at module import time. We do this
    # by monkey-patching the live Flask app — pytest doesn't share the
    # app across runs unless we use the client fixture, which we do.
    from service.api.decorators import app

    @app.route('/test/_force500', methods=['GET'])
    def _force500():
        raise ValueError("forced for test")

    # Snapshot the row count before.
    with db() as tx:
        before = tx.execute(
            "SELECT COUNT(*) AS c FROM system_error_log"
        ).fetchone()['c']

    # Hit the route. Expect 500 from Flask's default 500 handler chain.
    resp = client.get('/test/_force500')
    assert resp.status_code == 500

    with db() as tx:
        after = tx.execute(
            "SELECT COUNT(*) AS c FROM system_error_log"
        ).fetchone()['c']
        row = tx.execute(
            "SELECT path, method, exception_class, status_code "
            "FROM system_error_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert after == before + 1
    assert row['path'] == '/test/_force500'
    assert row['method'] == 'GET'
    assert row['exception_class'] == 'ValueError'
    assert row['status_code'] == 500
```

- [ ] **Step 4: Commit the wiring**

```bash
git add service/api/__init__.py tests/test_system_error_log.py
git commit -m "feat(observability): wire Sentry + error handler into Flask app"
```

### Task 1.4: Deploy Phase 1 + verify end-to-end

**Files:** None (deploy only).

- [ ] **Step 1: Push**

```bash
cd d:/Antigravity/ahavah-api
git push origin HEAD:ahavah/main
```

- [ ] **Step 2: Pull + rebuild + restart on droplet**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'cd /opt/ahavah-api && git pull origin ahavah/main && \
   set -a && source .env.production && set +a && \
   docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build api'
```

- [ ] **Step 3: Verify api is healthy**

```bash
curl -sk -m 10 https://api.ahavah.app/health -w "\nHTTP %{http_code}\n"
```

Expected: `status: ok` + `HTTP 200`.

- [ ] **Step 4: Force a real 500 (only once — this is destructive in the sense that it pollutes Sentry)**

Use the existing forced-500 mechanism if one exists, or hit a known-to-throw endpoint. Easiest: send a malformed POST to `/decisions`:

```bash
curl -sk -m 10 -X POST https://api.ahavah.app/decisions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer notvalid' \
  -d '{}'
```

(If that returns 4xx, find another endpoint that would 500 on bad input. The point is to produce ONE 5xx for verification.)

- [ ] **Step 5: Verify the row landed in system_error_log**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   "SELECT id, created_at, path, status_code, exception_class FROM system_error_log ORDER BY id DESC LIMIT 1;"'
```

Expected: one row with the path you just hit, status_code 500, an exception class name.

- [ ] **Step 6: Verify Sentry received the event**

Open `https://sentry.io/organizations/ahavah/issues/` in a browser. Expected: a new issue appears within 30s, titled with the exception class from step 5.

- [ ] **Step 7: Verify an alert email arrived**

Check `admin@techbaseltd.com`. Expected: one new email from Sentry titled "New Alert: …".

- [ ] **Step 8: Commit a marker that Phase 1 is complete**

```bash
git commit --allow-empty -m "milestone: Phase 1 (Sentry + system_error_log) verified end-to-end"
git push origin HEAD:ahavah/main
```

**Acceptance:** forced 5xx produced one DB row, one Sentry issue, one email — all within 30 seconds.

---

## Phase 2 — `cron_run_log` + `@cron_logged` decorator + daily prune

**Goal:** Every cron tick writes `running` → `success` / `error` rows to `cron_run_log`. Both new log tables prune at 03:00 UTC daily. The admin dashboard tile in Phase 4 will read from this table.

### Task 2.1: Add `cron_run_log` migration

**Files:**
- Create: `d:/Antigravity/ahavah-api/migrations/0028_cron_run_log.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- 0028_cron_run_log.sql
-- Per-tick log of every cron job execution. Idempotent.
-- See docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md §C4.
BEGIN;

CREATE TABLE IF NOT EXISTS cron_run_log (
  id            BIGSERIAL PRIMARY KEY,
  cron_name     TEXT        NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at   TIMESTAMPTZ,
  status        TEXT        NOT NULL
                CHECK (status IN ('running', 'success', 'error')),
  error_message TEXT,
  rows_affected INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cron_run_log_recent_per_cron
  ON cron_run_log (cron_name, started_at DESC);

COMMIT;
```

- [ ] **Step 2: Apply on droplet**

```bash
scp -i ~/.ssh/id_ed25519_ahavah d:/Antigravity/ahavah-api/migrations/0028_cron_run_log.sql root@167.71.93.27:/tmp/
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker cp /tmp/0028_cron_run_log.sql ahavah-api-postgres-1:/tmp/ && \
   docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -f /tmp/0028_cron_run_log.sql'
```

Expected: `BEGIN`, `CREATE TABLE`, `CREATE INDEX`, `COMMIT`.

- [ ] **Step 3: Verify the table**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c "\d cron_run_log"'
```

- [ ] **Step 4: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add migrations/0028_cron_run_log.sql
git commit -m "feat(observability): add cron_run_log table"
```

### Task 2.2: Write the `@cron_logged` decorator

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/observability/cron_logged.py`
- Test: `d:/Antigravity/ahavah-api/tests/test_cron_logged.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cron_logged.py
"""Tests for @cron_logged decorator. Verifies the running → success / error
transitions, and that the decorator re-raises so existing error handling
in service/cron/__init__.py:print_stacktrace still applies."""
from __future__ import annotations

import asyncio
import pytest


@pytest.mark.asyncio
async def test_cron_logged_success_path(db):
    from service.observability.cron_logged import cron_logged

    @cron_logged("test_cron_ok")
    async def ok():
        return None

    await ok()

    with db() as tx:
        rows = tx.execute(
            "SELECT cron_name, status, finished_at FROM cron_run_log "
            "WHERE cron_name = 'test_cron_ok' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]['status'] == 'success'
    assert rows[0]['finished_at'] is not None


@pytest.mark.asyncio
async def test_cron_logged_error_path_records_and_reraises(db):
    from service.observability.cron_logged import cron_logged

    @cron_logged("test_cron_fail")
    async def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        await boom()

    with db() as tx:
        rows = tx.execute(
            "SELECT status, error_message FROM cron_run_log "
            "WHERE cron_name = 'test_cron_fail' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]['status'] == 'error'
    assert 'kaboom' in rows[0]['error_message']
```

- [ ] **Step 2: Run the test, expect it to fail (module doesn't exist)**

```bash
docker exec ahavah-api-api-1 python -m pytest tests/test_cron_logged.py -v
```

Expected: `ModuleNotFoundError: No module named 'service.observability.cron_logged'`.

- [ ] **Step 3: Implement the decorator**

```python
# service/observability/cron_logged.py
"""@cron_logged(name) — wrap an async cron *_once() function so each
tick writes a row to cron_run_log.

Transitions:
  running  → INSERT at decorator entry; row id captured locally
  success  → UPDATE finished_at, status='success', rows_affected
             on normal return
  error    → UPDATE finished_at, status='error', error_message
             on exception, then re-raise

The decorator uses asyncdatabase (matches how existing cron modules
already write to Postgres). The 'running' write committing before the
work begins is the deliberate outbox-pattern half: if the api dies
mid-cron the row stays 'running' and the admin tile flags it as
'crashed' on read.

If the underlying function returns a dataclass / dict with a numeric
`rows_affected` attribute or key, that integer is recorded; otherwise
rows_affected stays NULL.
"""
from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from database.asyncdatabase import api_tx


_Q_INSERT_RUNNING = """
INSERT INTO cron_run_log (cron_name, status)
VALUES (%(name)s, 'running')
RETURNING id
"""

_Q_UPDATE_SUCCESS = """
UPDATE cron_run_log
   SET finished_at   = NOW(),
       status        = 'success',
       rows_affected = %(rows)s
 WHERE id = %(id)s
"""

_Q_UPDATE_ERROR = """
UPDATE cron_run_log
   SET finished_at   = NOW(),
       status        = 'error',
       error_message = %(msg)s
 WHERE id = %(id)s
"""


def _extract_rows_affected(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, int):
        return result
    rows = getattr(result, 'rows_affected', None)
    if isinstance(rows, int):
        return rows
    if isinstance(result, dict) and isinstance(result.get('rows_affected'), int):
        return result['rows_affected']
    return None


def cron_logged(name: str) -> Callable[[Callable[..., Awaitable[Any]]],
                                       Callable[..., Awaitable[Any]]]:
    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kw: Any) -> Any:
            async with api_tx() as tx:
                cur = await tx.execute(_Q_INSERT_RUNNING, dict(name=name))
                run_id = (await cur.fetchone())['id']
            try:
                result = await fn(*args, **kw)
            except Exception as e:
                async with api_tx() as tx:
                    await tx.execute(
                        _Q_UPDATE_ERROR,
                        dict(id=run_id, msg=str(e)[:1000]),
                    )
                raise
            async with api_tx() as tx:
                await tx.execute(
                    _Q_UPDATE_SUCCESS,
                    dict(id=run_id, rows=_extract_rows_affected(result)),
                )
            return result
        return wrapped
    return deco
```

- [ ] **Step 4: Install pytest-asyncio if it isn't already**

```bash
grep -q pytest-asyncio d:/Antigravity/ahavah-api/tests/requirements-test.txt || \
  echo 'pytest-asyncio==0.23.5' >> d:/Antigravity/ahavah-api/tests/requirements-test.txt
```

Also add `pytest.ini` (or `pyproject.toml`) entry if needed:

```bash
# Check if pytest already has asyncio mode configured
grep -l "asyncio_mode" d:/Antigravity/ahavah-api/pytest.ini d:/Antigravity/ahavah-api/pyproject.toml 2>/dev/null
```

If neither file has `asyncio_mode = auto`, add to `tests/conftest.py`:

```python
pytest_plugins = ('pytest_asyncio',)
```

- [ ] **Step 5: Run the test, expect it to pass**

```bash
docker exec ahavah-api-api-1 python -m pytest tests/test_cron_logged.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/observability/cron_logged.py tests/test_cron_logged.py \
        tests/requirements-test.txt tests/conftest.py
git commit -m "feat(observability): @cron_logged decorator writes cron_run_log rows"
```

### Task 2.3: Apply `@cron_logged` to every existing `_once()` cron function

**Files:**
- Modify each of: `service/cron/{autodeactivate2,betareengagement,checkphotos,garbagerecords,notifications,nsfwphotorunner,pendingdeletion,photocleaner,audiocleaner,verificationjobrunner,profilereporter}/__init__.py` (audit the actual list at start of task)

- [ ] **Step 1: Enumerate the *_once functions to wrap**

```bash
grep -rn "async def.*_once" d:/Antigravity/ahavah-api/service/cron/
```

This produces the exact list. Confirm each module's `_once` function name.

- [ ] **Step 2: For each, add the import and decorator**

Apply this pattern to every `_once` function:

```python
# At top of the module
from service.observability.cron_logged import cron_logged

# Above the function definition
@cron_logged('autodeactivate2')  # use the module's package name as the cron_name
async def autodeactivate2_once():
    ...
```

Use the module's package name (e.g. `autodeactivate2`, `betareengagement`, `checkphotos`, etc.) as the `cron_name` so the admin tile can group rows per cron unambiguously.

**The full mapping** (verify the function name in each module before editing):

| Module path | cron_name string | Existing function |
|---|---|---|
| `service/cron/autodeactivate2/__init__.py` | `'autodeactivate2'` | `autodeactivate2_once` (confirm) |
| `service/cron/betareengagement/__init__.py` | `'betareengagement'` | `send_beta_reengagement_once` |
| `service/cron/checkphotos/__init__.py` | `'checkphotos'` | `check_photos_once` (confirm) |
| `service/cron/garbagerecords/__init__.py` | `'garbagerecords'` | `delete_garbage_records_once` (confirm) |
| `service/cron/notifications/__init__.py` | `'notifications'` | `send_notifications_once` (confirm) |
| `service/cron/nsfwphotorunner/__init__.py` | `'nsfwphotorunner'` | `predict_nsfw_photos_once` (confirm) |
| `service/cron/pendingdeletion/__init__.py` | `'pendingdeletion'` | `hard_delete_expired_once` (confirm) |
| `service/cron/photocleaner/__init__.py` | `'photocleaner'` | `clean_photos_once` (confirm) |
| `service/cron/audiocleaner/__init__.py` | `'audiocleaner'` | `clean_audio_once` (confirm) |
| `service/cron/verificationjobrunner/__init__.py` | `'verificationjobrunner'` | `verify_once` (confirm) |
| `service/cron/profilereporter/__init__.py` | `'profilereporter'` | `report_profiles_once` (confirm) |

- [ ] **Step 3: For each module, make the surgical edit**

Example for `betareengagement` (apply the same pattern to all 11):

In `service/cron/betareengagement/__init__.py`:

```python
# Existing imports unchanged + add:
from service.observability.cron_logged import cron_logged

# Existing function:
@cron_logged('betareengagement')
async def send_beta_reengagement_once():
    ...   # body unchanged
```

- [ ] **Step 4: Smoke-test once locally per module (optional but recommended)**

For one module — say `betareengagement` — invoke the once function manually inside the container to confirm a row gets written:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-cron-1 python -c "
import asyncio
from service.cron.betareengagement import send_beta_reengagement_once
asyncio.run(send_beta_reengagement_once())
print(\"done\")
"'
```

Then check:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   "SELECT cron_name, status, started_at, finished_at FROM cron_run_log \
    WHERE cron_name = '"'"'betareengagement'"'"' ORDER BY id DESC LIMIT 1;"'
```

Expected: one row with status='success'.

- [ ] **Step 5: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/cron/
git commit -m "feat(observability): wrap all cron *_once() with @cron_logged"
```

### Task 2.4: Add `prunesystemlogs` daily cron

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/cron/prunesystemlogs/__init__.py`
- Modify: `d:/Antigravity/ahavah-api/service/cron/__init__.py` (add to gather)

- [ ] **Step 1: Create the cron module**

```python
# service/cron/prunesystemlogs/__init__.py
"""Daily prune of system_error_log and cron_run_log to 30-day retention.

Both tables grow with usage but are bounded. At sustained 1k errors/hr
the system_error_log would hit 720k rows at 30 days; both queries below
remain index-supported for that volume.

Runs once per day at ~03:00 UTC + a random 0-15 min jitter so a cluster
of crons doesn't synchronize."""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone

from database.asyncdatabase import api_tx
from service.cron.cronutil import MAX_RANDOM_START_DELAY, print_stacktrace
from service.observability.cron_logged import cron_logged


PRUNE_HOUR_UTC = int(os.environ.get('DUO_PRUNE_HOUR_UTC', '3'))
RETENTION_DAYS = int(os.environ.get('DUO_LOG_RETENTION_DAYS', '30'))


_Q_PRUNE_ERRORS = """
DELETE FROM system_error_log
 WHERE created_at < NOW() - (%(days)s || ' days')::INTERVAL
"""

_Q_PRUNE_CRONS = """
DELETE FROM cron_run_log
 WHERE started_at < NOW() - (%(days)s || ' days')::INTERVAL
"""


@cron_logged('prunesystemlogs')
async def prune_system_logs_once():
    async with api_tx() as tx:
        cur = await tx.execute(_Q_PRUNE_ERRORS, dict(days=RETENTION_DAYS))
        n_err = cur.rowcount
        cur = await tx.execute(_Q_PRUNE_CRONS, dict(days=RETENTION_DAYS))
        n_cron = cur.rowcount
    print(f'prunesystemlogs: deleted {n_err} error rows, {n_cron} cron rows')


def _seconds_until_next_prune() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=PRUNE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target.replace(day=target.day + 1)
    return (target - now).total_seconds()


async def prune_system_logs_forever():
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(prune_system_logs_once)
        await asyncio.sleep(_seconds_until_next_prune())
```

- [ ] **Step 2: Register in `service/cron/__init__.py`**

Open the file, find the existing import block (around lines 1-15 where `betareengagement` etc. are imported), and add:

```python
from service.cron.prunesystemlogs import prune_system_logs_forever
```

Then find the `asyncio.gather(...)` call inside `main()` and add the new call to its arg list:

```python
async def main():
    await asyncio.gather(
        # ... existing entries unchanged ...
        autodeactivate2_forever(),
        # ... rest unchanged ...
        prune_system_logs_forever(),   # ← add this
    )
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/cron/prunesystemlogs/ service/cron/__init__.py
git commit -m "feat(observability): daily prune of system_error_log + cron_run_log (30d retention)"
```

### Task 2.5: Deploy Phase 2 + verify

**Files:** None (deploy only).

- [ ] **Step 1: Push and redeploy api + cron**

```bash
cd d:/Antigravity/ahavah-api
git push origin HEAD:ahavah/main

ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'cd /opt/ahavah-api && git pull origin ahavah/main && \
   set -a && source .env.production && set +a && \
   docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build api cron'
```

- [ ] **Step 2: Wait 30 seconds, then verify rows are landing**

```bash
sleep 30
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   "SELECT cron_name, status, COUNT(*) FROM cron_run_log GROUP BY 1,2 ORDER BY 1;"'
```

Expected: at least 2-3 cron_name rows appearing with status='success'. Some crons run on long intervals (hours / days) so not all 12 will be visible in 30 seconds — that's fine.

- [ ] **Step 3: Commit milestone marker**

```bash
git commit --allow-empty -m "milestone: Phase 2 (cron_run_log + decorator + prune) deployed"
git push origin HEAD:ahavah/main
```

**Acceptance:** new rows arriving in `cron_run_log` per cron tick with `status='success'` transitions.

---

## Phase 3 — Five new admin endpoints

**Goal:** Five new admin endpoints under `/admin/system/*` return JSON readable by the admin dashboard. All gated by `require_admin`.

### Task 3.1: Add admin queries for the four read endpoints

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/admin/queries/system_observability.py`
- Modify: `d:/Antigravity/ahavah-api/service/admin/queries/__init__.py` (re-export)

- [ ] **Step 1: Write the queries**

```python
# service/admin/queries/system_observability.py
"""SQL for the Phase 3 /admin/system/{errors,crons} endpoints.
Uptime + containers do not query Postgres so live elsewhere."""

# /admin/system/errors?since=24h|7d|30d
# Window is bind-parameterized as an interval string ('24 hours', '7 days', …).
Q_RECENT_ERRORS = """
SELECT id,
       created_at,
       path,
       method,
       status_code,
       exception_class,
       traceback,
       person_id
  FROM system_error_log
 WHERE created_at > NOW() - (%(window)s)::INTERVAL
 ORDER BY created_at DESC
 LIMIT 50
"""


# /admin/system/crons — latest run per cron + 24h success-rate metric.
Q_CRON_SUMMARY = """
WITH latest AS (
    SELECT DISTINCT ON (cron_name)
           cron_name,
           status,
           started_at,
           finished_at,
           error_message,
           rows_affected
      FROM cron_run_log
     ORDER BY cron_name, started_at DESC
),
rates_24h AS (
    SELECT cron_name,
           COUNT(*)                                    AS runs_24h,
           COUNT(*) FILTER (WHERE status = 'success')  AS ok_24h,
           COUNT(*) FILTER (WHERE status = 'error')    AS err_24h,
           MAX(finished_at - started_at)               AS longest_run_24h
      FROM cron_run_log
     WHERE started_at > NOW() - INTERVAL '24 hours'
     GROUP BY cron_name
)
SELECT l.cron_name,
       l.status                                                AS last_status,
       l.started_at                                            AS last_started_at,
       l.finished_at                                           AS last_finished_at,
       l.error_message                                         AS last_error,
       l.rows_affected                                         AS last_rows_affected,
       COALESCE(r.runs_24h, 0)                                 AS runs_24h,
       COALESCE(r.ok_24h, 0)                                   AS ok_24h,
       COALESCE(r.err_24h, 0)                                  AS err_24h,
       EXTRACT(EPOCH FROM r.longest_run_24h)::int              AS longest_run_24h_seconds
  FROM latest l
  LEFT JOIN rates_24h r USING (cron_name)
 ORDER BY l.cron_name
"""


# Optional: extension to /admin/system/health that adds disk + memory.
# These read pg_settings + pg_database_size; the actual disk/memory of
# the host is read from inside the container in service/observability/
# docker_inspect.py (Task 3.5).
Q_DB_SIZE = """
SELECT
  pg_database_size('duo_api')                AS bytes,
  pg_size_pretty(pg_database_size('duo_api')) AS pretty
"""
```

- [ ] **Step 2: Re-export from the queries `__init__.py`**

Open `d:/Antigravity/ahavah-api/service/admin/queries/__init__.py`. Add at the bottom:

```python
from service.admin.queries.system_observability import (
    Q_RECENT_ERRORS,
    Q_CRON_SUMMARY,
    Q_DB_SIZE,
)
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/admin/queries/system_observability.py service/admin/queries/__init__.py
git commit -m "feat(admin): SQL for /admin/system/errors + /crons"
```

### Task 3.2: Write the UptimeRobot REST client + 30s cache

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/observability/uptimerobot.py`

- [ ] **Step 1: Implement the client**

```python
# service/observability/uptimerobot.py
"""Thin client over UptimeRobot's getMonitors endpoint.

Returns the fields the admin /admin/system/uptime endpoint needs:
  current status, 24h / 7d / 30d uptime %, last 50 log events.

Cached server-side for 30s (one cache bucket per 30s wall-clock slot)
so the dashboard polling at 30s doesn't drive us past UR's free-tier
rate limit (10 req/min).

Network failures return a sentinel {available: False, ...} dict rather
than raising — the dashboard renders a 'data unavailable' state."""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import requests


_API_BASE = 'https://api.uptimerobot.com/v2'
_TIMEOUT_S = 8

# 30-second cache. (bucket_id, payload).
_cache: tuple[int, Dict[str, Any] | None] = (-1, None)


def _api_key() -> str | None:
    return os.environ.get('UPTIMEROBOT_READ_API_KEY')


def _fetch() -> Dict[str, Any]:
    key = _api_key()
    if not key:
        return {'available': False, 'reason': 'no_api_key'}
    try:
        resp = requests.post(
            f'{_API_BASE}/getMonitors',
            data={
                'api_key': key,
                'format': 'json',
                'logs': 1,
                'logs_limit': 50,
                'response_times': 0,
                'custom_uptime_ratios': '1-7-30',  # 24h / 7d / 30d
            },
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return {'available': False, 'reason': f'network: {e!s}'}

    monitors = data.get('monitors') or []
    if not monitors:
        return {'available': True, 'monitors': []}

    out_monitors = []
    for m in monitors:
        ratios = (m.get('custom_uptime_ratio') or '').split('-')
        out_monitors.append({
            'id':            m.get('id'),
            'friendly_name': m.get('friendly_name'),
            'url':           m.get('url'),
            'status':        _STATUS_MAP.get(m.get('status'), 'unknown'),
            'uptime_24h':    _safe_float(ratios, 0),
            'uptime_7d':     _safe_float(ratios, 1),
            'uptime_30d':    _safe_float(ratios, 2),
            'logs':          [
                {
                    'type':     _LOG_TYPE_MAP.get(l.get('type'), 'unknown'),
                    'datetime': l.get('datetime'),
                    'duration': l.get('duration'),
                    'reason':   (l.get('reason') or {}).get('detail'),
                }
                for l in (m.get('logs') or [])[:50]
            ],
        })
    return {'available': True, 'monitors': out_monitors}


_STATUS_MAP = {0: 'paused', 1: 'not_checked', 2: 'up', 8: 'seems_down', 9: 'down'}
_LOG_TYPE_MAP = {1: 'down', 2: 'up', 3: 'paused', 99: 'started'}


def _safe_float(parts: list[str], i: int) -> float | None:
    try:
        return float(parts[i])
    except (IndexError, ValueError):
        return None


def get_uptime() -> Dict[str, Any]:
    """Returns cached UR payload, refreshing if the 30s bucket rolled."""
    global _cache
    bucket = int(time.time()) // 30
    if _cache[0] == bucket and _cache[1] is not None:
        return _cache[1]
    payload = _fetch()
    _cache = (bucket, payload)
    return payload
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/observability/uptimerobot.py
git commit -m "feat(observability): UptimeRobot REST client + 30s cache"
```

### Task 3.3: Write the Docker inspect helper

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/observability/docker_inspect.py`

- [ ] **Step 1: Implement the client**

```python
# service/observability/docker_inspect.py
"""Read container state from the Docker Engine API via the host socket.

Requires the api container to have /var/run/docker.sock mounted read-only.
If the socket isn't reachable, returns {available: False}. The admin
dashboard tile renders a 'data unavailable' state in that case.

Returns one row per container that has the 'ahavah' label (matching
the autoheal scope in docker-compose.production.yml). The label allows
the watchdog and this query to share the same scope of interest."""
from __future__ import annotations

import os
from typing import Any, Dict

import requests
import requests_unixsocket  # type: ignore  # docker socket


_SOCKET = os.environ.get('DOCKER_SOCKET', '/var/run/docker.sock')
_TIMEOUT_S = 5


def _session() -> requests.Session:
    s = requests_unixsocket.Session()
    return s


def get_containers() -> Dict[str, Any]:
    """Returns [{name, status, health, started_at, image}] for ahavah-labeled
    containers, or {available: False} if the socket can't be read."""
    if not os.path.exists(_SOCKET):
        return {'available': False, 'reason': 'no_socket'}
    encoded = _SOCKET.replace('/', '%2F')
    url = (
        f'http+unix://{encoded}/v1.42/containers/json'
        f'?all=true&filters=%7B%22label%22%3A%5B%22ahavah%22%5D%7D'
    )
    try:
        resp = _session().get(url, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        items = resp.json()
    except (requests.RequestException, ValueError) as e:
        return {'available': False, 'reason': f'docker: {e!s}'}

    out = []
    for c in items:
        names = [n.lstrip('/') for n in (c.get('Names') or [])]
        out.append({
            'name':       names[0] if names else None,
            'image':      c.get('Image'),
            'state':      c.get('State'),
            'status':     c.get('Status'),
            'started_at': c.get('Created'),
            'health':     _extract_health(c.get('Status') or ''),
            'labels':     c.get('Labels') or {},
        })
    return {'available': True, 'containers': out}


def _extract_health(status: str) -> str:
    if '(healthy)' in status:
        return 'healthy'
    if '(unhealthy)' in status:
        return 'unhealthy'
    if '(health: starting)' in status:
        return 'starting'
    return 'unknown'
```

- [ ] **Step 2: Add `requests_unixsocket` to requirements.txt**

```bash
echo 'requests_unixsocket==0.4.1' >> d:/Antigravity/ahavah-api/requirements.txt
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/observability/docker_inspect.py requirements.txt
git commit -m "feat(observability): docker engine API client for container health"
```

### Task 3.4: Write the four admin endpoint handlers

**Files:**
- Create: `d:/Antigravity/ahavah-api/service/api/admin/system_observability_routes.py`
- Test: `d:/Antigravity/ahavah-api/tests/test_admin_system_observability_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_system_observability_routes.py
"""Endpoint-shape tests for the four /admin/system/* observability
routes. The actual queries are integration-tested via the live smoke
step at the end of Phase 3."""


def test_module_imports_register_all_four_handlers():
    """If imports succeed, all four handlers are registered via @aget."""
    import service.api.admin.system_observability_routes as m
    assert hasattr(m, 'get_admin_system_errors')
    assert hasattr(m, 'get_admin_system_crons')
    assert hasattr(m, 'get_admin_system_uptime')
    assert hasattr(m, 'get_admin_system_containers')


def test_window_validator_rejects_unknown():
    from service.api.admin.system_observability_routes import _resolve_window
    assert _resolve_window('24h') == '24 hours'
    assert _resolve_window('7d')  == '7 days'
    assert _resolve_window('30d') == '30 days'
    assert _resolve_window(None)  == '24 hours'   # default
    assert _resolve_window('bad') == '24 hours'   # fall back, not raise
```

- [ ] **Step 2: Run, expect failure**

```bash
docker exec ahavah-api-api-1 python -m pytest tests/test_admin_system_observability_routes.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the routes**

```python
# service/api/admin/system_observability_routes.py
"""Phase 3 — observability admin endpoints. All gated by require_admin.

Endpoints:
  GET /admin/system/errors?since=24h|7d|30d
  GET /admin/system/crons
  GET /admin/system/uptime
  GET /admin/system/containers
"""
from __future__ import annotations

from flask import request

import duotypes as t

from database import api_tx
from service.admin import require_admin
from service.admin.queries import Q_RECENT_ERRORS, Q_CRON_SUMMARY
from service.api.decorators import aget
from service.observability.docker_inspect import get_containers
from service.observability.uptimerobot import get_uptime


_WINDOWS = {
    '24h': '24 hours',
    '7d':  '7 days',
    '30d': '30 days',
}


def _resolve_window(arg: str | None) -> str:
    if arg in _WINDOWS:
        return _WINDOWS[arg]
    return _WINDOWS['24h']


@aget('/admin/system/errors')
def get_admin_system_errors(s: t.SessionInfo):
    require_admin(s)
    window = _resolve_window(request.args.get('since'))
    with api_tx('read committed') as tx:
        rows = tx.execute(Q_RECENT_ERRORS, dict(window=window)).fetchall()
    return {
        'window':  window,
        'errors':  [dict(r) for r in rows],
    }


@aget('/admin/system/crons')
def get_admin_system_crons(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        rows = tx.execute(Q_CRON_SUMMARY).fetchall()
    return {'crons': [dict(r) for r in rows]}


@aget('/admin/system/uptime')
def get_admin_system_uptime(s: t.SessionInfo):
    require_admin(s)
    return get_uptime()


@aget('/admin/system/containers')
def get_admin_system_containers(s: t.SessionInfo):
    require_admin(s)
    return get_containers()
```

- [ ] **Step 4: Register the routes module in `service/api/__init__.py`**

Open the file, find the existing admin route imports (around lines 1052-1059), and append:

```python
import service.api.admin.system_observability_routes  # noqa: E402,F401
```

- [ ] **Step 5: Run the test, expect pass**

```bash
docker exec ahavah-api-api-1 python -m pytest tests/test_admin_system_observability_routes.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/api/admin/system_observability_routes.py \
        service/api/__init__.py \
        tests/test_admin_system_observability_routes.py
git commit -m "feat(admin): /admin/system/{errors,crons,uptime,containers} endpoints"
```

### Task 3.5: Extend existing `/admin/system/health` with disk + memory

**Files:**
- Modify: `d:/Antigravity/ahavah-api/service/api/admin/system_routes.py`

- [ ] **Step 1: Add disk + memory fields**

Replace the body of `get_system_health` in `system_routes.py` with:

```python
@aget('/admin/system/health')
def get_system_health(s: t.SessionInfo):
    require_admin(s)
    import shutil
    import psutil  # add to requirements.txt
    from service.admin.queries import Q_DB_SIZE

    with api_tx('read committed') as tx:
        health = tx.execute(Q_SYSTEM_HEALTH).fetchone() or {}
        otp    = tx.execute(Q_OTP_24H).fetchone() or {}
        dbsize = tx.execute(Q_DB_SIZE).fetchone() or {}
    disk = shutil.disk_usage('/')
    vmem = psutil.virtual_memory()
    return {
        'health':  dict(health),
        'otp':     dict(otp),
        'dbsize':  dict(dbsize),
        'disk': {
            'total_bytes': disk.total,
            'used_bytes':  disk.used,
            'free_bytes':  disk.free,
            'used_pct':    round(disk.used / disk.total * 100, 1),
        },
        'memory': {
            'total_bytes': vmem.total,
            'used_bytes':  vmem.used,
            'free_bytes':  vmem.available,
            'used_pct':    vmem.percent,
        },
    }
```

- [ ] **Step 2: Add `psutil` to requirements.txt**

```bash
echo 'psutil==6.0.0' >> d:/Antigravity/ahavah-api/requirements.txt
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add service/api/admin/system_routes.py requirements.txt
git commit -m "feat(admin): /admin/system/health adds disk + memory usage"
```

### Task 3.6: Mount docker socket on api container

**Files:**
- Modify: `d:/Antigravity/ahavah-api/docker-compose.production.yml`

- [ ] **Step 1: Mount the socket read-only on the api service**

In the api service's `volumes:` block, add:

```yaml
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

(Keep all existing volumes. Adding this line is the change.)

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add docker-compose.production.yml
git commit -m "ops(compose): mount docker.sock readonly on api for /admin/system/containers"
git push origin HEAD:ahavah/main
```

### Task 3.7: Deploy Phase 3 + verify endpoints

**Files:** None (deploy only).

- [ ] **Step 1: Pull + rebuild api**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'cd /opt/ahavah-api && git pull origin ahavah/main && \
   set -a && source .env.production && set +a && \
   docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build api'
```

- [ ] **Step 2: Verify each endpoint returns 200 with a real admin bearer**

The admin bearer can be captured from a browser session signed into admin.ahavah.app — DevTools → Application → Local Storage → `admin.sessionToken`.

```bash
BEARER='<paste admin bearer here>'
for path in errors crons uptime containers health; do
  echo "=== /admin/system/$path ==="
  curl -sk -m 10 "https://api.ahavah.app/admin/system/$path" \
    -H "Authorization: Bearer $BEARER" -w "\nHTTP %{http_code}\n" \
    | head -10
done
```

Expected:
- `errors` → JSON with `window` + `errors` list (may be empty)
- `crons` → JSON with `crons` list (likely ≥ 5 entries)
- `uptime` → JSON with `available: true` + `monitors` list
- `containers` → JSON with `available: true` + ~5 containers
- `health` → JSON with `health`, `otp`, `dbsize`, `disk`, `memory`

- [ ] **Step 3: Confirm 403 for non-admin**

```bash
curl -sk -m 10 https://api.ahavah.app/admin/system/errors \
  -H 'Authorization: Bearer notvalid' \
  -w "\nHTTP %{http_code}\n"
```

Expected: `403` or `401` (depending on bearer parse failure semantics).

- [ ] **Step 4: Milestone marker**

```bash
git commit --allow-empty -m "milestone: Phase 3 (5 admin endpoints) verified"
git push origin HEAD:ahavah/main
```

**Acceptance:** all five endpoints return 200 for admin bearer with the JSON shapes above; 403 for non-admin.

---

## Phase 4 — Five admin dashboard tiles

**Goal:** The System tab in `admin.ahavah.app/` renders four new tiles + extends the existing Health tile. Each tile loads, shows skeleton while loading, shows an empty state when no data, and shows an error state when its underlying API call fails.

### Task 4.1: Add TypeScript types for the four new responses

**Files:**
- Modify: `d:/Antigravity/ahavah-admin/src/lib/types.ts`

- [ ] **Step 1: Append the four new types**

Find the existing `SystemHealthResponse` type and append below it:

```ts
export type UptimeResponse =
  | {
      available: true;
      monitors: ReadonlyArray<{
        id: number;
        friendly_name: string;
        url: string;
        status: 'paused' | 'not_checked' | 'up' | 'seems_down' | 'down' | 'unknown';
        uptime_24h: number | null;
        uptime_7d: number | null;
        uptime_30d: number | null;
        logs: ReadonlyArray<{
          type: 'down' | 'up' | 'paused' | 'started' | 'unknown';
          datetime: number;
          duration: number;
          reason: string | null;
        }>;
      }>;
    }
  | { available: false; reason: string };

export type ErrorsResponse = {
  window: string;
  errors: ReadonlyArray<{
    id: number;
    created_at: string;
    path: string;
    method: string;
    status_code: number;
    exception_class: string;
    traceback: string;
    person_id: number | null;
  }>;
};

export type ContainersResponse =
  | {
      available: true;
      containers: ReadonlyArray<{
        name: string | null;
        image: string;
        state: string;
        status: string;
        started_at: number;
        health: 'healthy' | 'unhealthy' | 'starting' | 'unknown';
        labels: Record<string, string>;
      }>;
    }
  | { available: false; reason: string };

export type CronStatusResponse = {
  crons: ReadonlyArray<{
    cron_name: string;
    last_status: 'running' | 'success' | 'error';
    last_started_at: string;
    last_finished_at: string | null;
    last_error: string | null;
    last_rows_affected: number | null;
    runs_24h: number;
    ok_24h: number;
    err_24h: number;
    longest_run_24h_seconds: number | null;
  }>;
};
```

- [ ] **Step 2: Extend SystemHealthResponse with the new fields**

Replace the existing `SystemHealthResponse` type with:

```ts
export type SystemHealthResponse = {
  health: {
    signups_today: number; signups_7d: number; signups_30d: number;
    dau: number; wau: number; mau: number;
    waitlist_total: number; beta_total: number; photo_total: number;
    referral_total: number; admin_actions_total: number;
  };
  otp:    { sent_24h: number };
  dbsize: { bytes: number; pretty: string };
  disk:   { total_bytes: number; used_bytes: number; free_bytes: number; used_pct: number };
  memory: { total_bytes: number; used_bytes: number; free_bytes: number; used_pct: number };
};
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/lib/types.ts
git commit -m "feat(admin): types for system observability endpoints"
```

### Task 4.2: Add the four React Query hooks

**Files:**
- Modify: `d:/Antigravity/ahavah-admin/src/lib/queries.ts`

- [ ] **Step 1: Append the hooks**

At the bottom of `src/lib/queries.ts`, add:

```ts
import type {
  UptimeResponse, ErrorsResponse, ContainersResponse, CronStatusResponse,
} from "@/lib/types";

export function useUptime() {
  return useQuery<UptimeResponse>({
    queryKey: ["system", "uptime"],
    queryFn: () => api.get<UptimeResponse>("/admin/system/uptime"),
    refetchInterval: 30_000,
  });
}

export function useErrors(since: "24h" | "7d" | "30d" = "24h") {
  return useQuery<ErrorsResponse>({
    queryKey: ["system", "errors", since],
    queryFn: () => api.get<ErrorsResponse>(`/admin/system/errors?since=${since}`),
    refetchInterval: 30_000,
  });
}

export function useContainers() {
  return useQuery<ContainersResponse>({
    queryKey: ["system", "containers"],
    queryFn: () => api.get<ContainersResponse>("/admin/system/containers"),
    refetchInterval: 10_000,
  });
}

export function useCronStatus() {
  return useQuery<CronStatusResponse>({
    queryKey: ["system", "crons"],
    queryFn: () => api.get<CronStatusResponse>("/admin/system/crons"),
    refetchInterval: 30_000,
  });
}
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/lib/queries.ts
git commit -m "feat(admin): React Query hooks for system observability"
```

### Task 4.3: Build the Uptime tile

**Files:**
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/system/tile-uptime.tsx`

- [ ] **Step 1: Implement the tile**

```tsx
// src/components/admin/system/tile-uptime.tsx
"use client";

import { CheckCircle2, AlertCircle } from "lucide-react";

import { useUptime } from "@/lib/queries";
import { AdminCard, EmptyState } from "@/components/admin/design-primitives";
import { Skeleton } from "@/components/ui/skeleton";

export function TileUptime() {
  const { data, isLoading, isError } = useUptime();

  if (isError) {
    return (
      <AdminCard>
        <EmptyState title="Couldn't load uptime data" />
      </AdminCard>
    );
  }

  if (isLoading || !data) {
    return (
      <AdminCard>
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-4 w-40" />
        </div>
      </AdminCard>
    );
  }

  if (data.available === false) {
    return (
      <AdminCard>
        <EmptyState
          title="UptimeRobot data unavailable"
          description={data.reason}
        />
      </AdminCard>
    );
  }

  const m = data.monitors[0];
  if (!m) {
    return (
      <AdminCard>
        <EmptyState title="No monitors configured in UptimeRobot" />
      </AdminCard>
    );
  }

  const Icon = m.status === "up" ? CheckCircle2 : AlertCircle;
  const statusColor =
    m.status === "up" ? "text-(--good)" :
    m.status === "down" ? "text-(--bad)" :
    "text-(--warn)";

  return (
    <AdminCard>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] font-extrabold tracking-[0.1em] uppercase text-(--faint)">
          Uptime
        </div>
        <Icon className={`size-4 ${statusColor}`} />
      </div>

      <div className="flex gap-[18px] mb-2">
        <Stat label="24h" value={m.uptime_24h} />
        <Stat label="7d"  value={m.uptime_7d} />
        <Stat label="30d" value={m.uptime_30d} />
      </div>

      <div className="text-[10px] uppercase tracking-[0.1em] text-(--faint) mt-2">
        {m.friendly_name} · {m.status}
      </div>
    </AdminCard>
  );
}

function Stat({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="min-w-0">
      <div className="font-display text-[28px] leading-none tracking-[-0.01em] tabular-nums text-(--ink)">
        {value === null ? "—" : `${value.toFixed(2)}%`}
      </div>
      <div className="text-[10px] font-extrabold tracking-[0.1em] uppercase text-(--faint) mt-1">
        {label}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/components/admin/system/tile-uptime.tsx
git commit -m "feat(admin): TileUptime"
```

### Task 4.4: Build the Errors tile

**Files:**
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/system/tile-errors.tsx`

- [ ] **Step 1: Implement the tile**

```tsx
// src/components/admin/system/tile-errors.tsx
"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";

import { useErrors } from "@/lib/queries";
import { AdminCard, EmptyState } from "@/components/admin/design-primitives";
import { Skeleton } from "@/components/ui/skeleton";

export function TileErrors() {
  const [since, setSince] = useState<"24h" | "7d" | "30d">("24h");
  const [expanded, setExpanded] = useState<number | null>(null);
  const { data, isLoading, isError } = useErrors(since);

  if (isError) {
    return (
      <AdminCard>
        <EmptyState title="Couldn't load error log" />
      </AdminCard>
    );
  }

  return (
    <AdminCard>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] font-extrabold tracking-[0.1em] uppercase text-(--faint)">
          Errors ({data?.errors.length ?? 0})
        </div>
        <div className="flex gap-1">
          {(["24h", "7d", "30d"] as const).map((w) => (
            <button
              key={w}
              onClick={() => setSince(w)}
              className={
                "text-[10px] uppercase tracking-[0.1em] px-2 py-1 rounded " +
                (since === w
                  ? "bg-(--card-2) text-(--ink)"
                  : "text-(--faint) hover:text-(--ink)")
              }
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {isLoading || !data ? (
        <div className="flex flex-col gap-1">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-6 w-full" />
          ))}
        </div>
      ) : data.errors.length === 0 ? (
        <EmptyState title="No errors in window" />
      ) : (
        <div className="flex flex-col gap-1">
          {data.errors.slice(0, 10).map((e) => {
            const isOpen = expanded === e.id;
            return (
              <div
                key={e.id}
                className="border border-(--hairline) rounded text-[11px]"
              >
                <button
                  onClick={() => setExpanded(isOpen ? null : e.id)}
                  className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-(--card-2)"
                >
                  {isOpen ? (
                    <ChevronDown className="size-3 text-(--faint) shrink-0" />
                  ) : (
                    <ChevronRight className="size-3 text-(--faint) shrink-0" />
                  )}
                  <span className="text-(--bad) tabular-nums w-10 shrink-0">
                    {e.status_code}
                  </span>
                  <span className="text-(--ink) truncate flex-1">
                    {e.exception_class}: {e.method} {e.path}
                  </span>
                  <span className="text-(--faint) tabular-nums shrink-0">
                    {new Date(e.created_at).toISOString().slice(11, 19)}
                  </span>
                </button>
                {isOpen && (
                  <pre className="text-[10px] text-(--ink-2) bg-(--card-2) p-2 m-0 overflow-x-auto whitespace-pre-wrap break-words">
                    {e.traceback}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      )}
    </AdminCard>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/components/admin/system/tile-errors.tsx
git commit -m "feat(admin): TileErrors with expandable traceback"
```

### Task 4.5: Build the Containers tile

**Files:**
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/system/tile-containers.tsx`

- [ ] **Step 1: Implement the tile**

```tsx
// src/components/admin/system/tile-containers.tsx
"use client";

import { CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

import { useContainers } from "@/lib/queries";
import { AdminCard, EmptyState } from "@/components/admin/design-primitives";
import { Skeleton } from "@/components/ui/skeleton";

const HEALTH_ICON = {
  healthy:  CheckCircle2,
  unhealthy: AlertCircle,
  starting:  Loader2,
  unknown:   AlertCircle,
} as const;

const HEALTH_COLOR = {
  healthy:  "text-(--good)",
  unhealthy: "text-(--bad)",
  starting:  "text-(--warn) animate-spin",
  unknown:   "text-(--faint)",
} as const;

export function TileContainers() {
  const { data, isLoading, isError } = useContainers();

  if (isError) {
    return (
      <AdminCard>
        <EmptyState title="Couldn't load containers" />
      </AdminCard>
    );
  }

  if (isLoading || !data) {
    return (
      <AdminCard>
        <Skeleton className="h-4 w-24 mb-3" />
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full mb-1" />
        ))}
      </AdminCard>
    );
  }

  if (data.available === false) {
    return (
      <AdminCard>
        <EmptyState title="Docker data unavailable" description={data.reason} />
      </AdminCard>
    );
  }

  return (
    <AdminCard>
      <div className="text-[10px] font-extrabold tracking-[0.1em] uppercase text-(--faint) mb-3">
        Containers ({data.containers.length})
      </div>
      <div className="flex flex-col gap-1">
        {data.containers.map((c) => {
          const Icon = HEALTH_ICON[c.health];
          const color = HEALTH_COLOR[c.health];
          return (
            <div key={c.name} className="flex items-center gap-2 text-[12px]">
              <Icon className={`size-3.5 shrink-0 ${color}`} />
              <span className="text-(--ink) truncate flex-1">{c.name}</span>
              <span className="text-(--faint) shrink-0">{c.health}</span>
            </div>
          );
        })}
      </div>
    </AdminCard>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/components/admin/system/tile-containers.tsx
git commit -m "feat(admin): TileContainers"
```

### Task 4.6: Build the Cron Status tile

**Files:**
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/system/tile-cron-status.tsx`

- [ ] **Step 1: Implement the tile**

```tsx
// src/components/admin/system/tile-cron-status.tsx
"use client";

import { CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

import { useCronStatus } from "@/lib/queries";
import { AdminCard, EmptyState } from "@/components/admin/design-primitives";
import { Skeleton } from "@/components/ui/skeleton";

function formatAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function flagCrashed(lastStartedAt: string, lastStatus: string): boolean {
  if (lastStatus !== "running") return false;
  const ms = Date.now() - new Date(lastStartedAt).getTime();
  return ms > 60 * 60_000;
}

export function TileCronStatus() {
  const { data, isLoading, isError } = useCronStatus();

  if (isError) {
    return (
      <AdminCard>
        <EmptyState title="Couldn't load cron status" />
      </AdminCard>
    );
  }

  if (isLoading || !data) {
    return (
      <AdminCard>
        <Skeleton className="h-4 w-24 mb-3" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-5 w-full mb-1" />
        ))}
      </AdminCard>
    );
  }

  if (data.crons.length === 0) {
    return (
      <AdminCard>
        <EmptyState title="No cron runs recorded yet" />
      </AdminCard>
    );
  }

  return (
    <AdminCard>
      <div className="text-[10px] font-extrabold tracking-[0.1em] uppercase text-(--faint) mb-3">
        Cron status
      </div>
      <div className="flex flex-col gap-1">
        {data.crons.map((c) => {
          const crashed = flagCrashed(c.last_started_at, c.last_status);
          const effective = crashed ? "crashed" : c.last_status;
          const Icon =
            effective === "success" ? CheckCircle2 :
            effective === "running" ? Loader2 :
            AlertCircle;
          const color =
            effective === "success" ? "text-(--good)" :
            effective === "running" ? "text-(--warn) animate-spin" :
            "text-(--bad)";
          const rate =
            c.runs_24h > 0 ? Math.round((c.ok_24h / c.runs_24h) * 100) : null;
          return (
            <div
              key={c.cron_name}
              className="flex items-center gap-2 text-[12px]"
            >
              <Icon className={`size-3.5 shrink-0 ${color}`} />
              <span className="text-(--ink) truncate flex-1">{c.cron_name}</span>
              <span className="text-(--faint) tabular-nums shrink-0 text-[11px]">
                {formatAgo(c.last_started_at)}
              </span>
              {rate !== null && (
                <span
                  className={
                    "text-[10px] tabular-nums shrink-0 " +
                    (rate === 100 ? "text-(--good)" :
                     rate >= 80 ? "text-(--warn)" : "text-(--bad)")
                  }
                >
                  {rate}%
                </span>
              )}
            </div>
          );
        })}
      </div>
    </AdminCard>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/components/admin/system/tile-cron-status.tsx
git commit -m "feat(admin): TileCronStatus with crashed detection"
```

### Task 4.7: Mount the four new tiles in `tab-system.tsx`

**Files:**
- Modify: `d:/Antigravity/ahavah-admin/src/components/admin/tab-system.tsx`

- [ ] **Step 1: Insert the four new tiles**

At the top of the file, add imports:

```tsx
import { TileUptime }     from "@/components/admin/system/tile-uptime";
import { TileErrors }     from "@/components/admin/system/tile-errors";
import { TileContainers } from "@/components/admin/system/tile-containers";
import { TileCronStatus } from "@/components/admin/system/tile-cron-status";
```

Inside the existing `TabSystem` component, after the page heading and before the existing health card, add a grid:

```tsx
<div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
  <TileUptime />
  <TileContainers />
  <TileErrors />
  <TileCronStatus />
</div>
```

- [ ] **Step 2: Surface the new disk + memory fields in the existing Health tile**

Inside `TabSystem`, locate the existing rendering of `health` data. Add below the existing `TripleStat` for waitlist/beta/photo:

```tsx
{h && data?.disk && data?.memory && (
  <>
    <TripleStat
      items={[
        { label: "disk %",   value: data.disk.used_pct },
        { label: "memory %", value: data.memory.used_pct },
        { label: "db",       value: undefined },  // pretty rendered below
      ]}
    />
    <div className="text-[11px] text-(--faint) mt-1">
      DB size: {data.dbsize?.pretty ?? "—"}
    </div>
  </>
)}
```

- [ ] **Step 3: Commit**

```bash
cd d:/Antigravity/ahavah-admin
git add src/components/admin/tab-system.tsx
git commit -m "feat(admin): mount Uptime+Errors+Containers+Cron tiles on System tab"
```

### Task 4.8: Deploy Phase 4 + verify in browser

**Files:** None (deploy only).

- [ ] **Step 1: Push**

```bash
cd d:/Antigravity/ahavah-admin
git push origin master
```

Vercel auto-deploys.

- [ ] **Step 2: Open admin.ahavah.app in browser**

Navigate to admin.ahavah.app, sign in, click the System tab.

- [ ] **Step 3: Verify each tile**

Expected:
- **Uptime tile** — shows "ahavah-api /health · up" with 24h/7d/30d percentages
- **Containers tile** — shows 5 rows (api, chat, cron, postgres, autoheal — autoheal absent until Phase 5)
- **Errors tile** — shows recent 5xx with `24h | 7d | 30d` window toggle; clicking an error expands the traceback
- **Cron tile** — shows N cron rows with most-recent run time and 24h success rate
- **Health tile (existing)** — now also shows disk % + memory % + DB size

- [ ] **Step 4: Test loading + error states**

Stop the api momentarily to verify the error states render correctly:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 'docker stop ahavah-api-api-1'
```

In the browser, refresh the System tab. Expected: each tile renders an "Couldn't load …" empty state with the title visible (no infinite spinner).

Restart:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 'docker start ahavah-api-api-1'
```

Refresh. Tiles should load again within 30 seconds.

- [ ] **Step 5: Milestone marker**

```bash
git commit --allow-empty -m "milestone: Phase 4 (4 new tiles + extended Health) deployed"
git push origin master
```

**Acceptance:** all 5 tiles visible on System tab, loading states + error states render gracefully.

---

## Phase 5 — `autoheal` sidecar

**Goal:** Container marked unhealthy >2 min auto-restarts without human intervention.

### Task 5.1: Add `autoheal` service to `docker-compose.production.yml`

**Files:**
- Modify: `d:/Antigravity/ahavah-api/docker-compose.production.yml`

- [ ] **Step 1: Add the autoheal service**

At the bottom of the `services:` block, append:

```yaml
  autoheal:
    image: willfarrell/autoheal:1.2.0
    container_name: autoheal
    restart: always
    environment:
      AUTOHEAL_CONTAINER_LABEL: ahavah
      AUTOHEAL_DEFAULT_STOP_TIMEOUT: '10'
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

- [ ] **Step 2: Add `ahavah` label to api/chat/cron/postgres**

To each of those four services, add (alongside existing `environment:`, `volumes:`, etc.):

```yaml
    labels:
      - ahavah=true
```

(The exact format autoheal accepts is `KEY=VALUE` where it watches containers whose `labels.{KEY}` equals `'true'`. Verify against autoheal docs if the actual matcher is stricter.)

- [ ] **Step 3: Verify each container has a healthcheck directive**

For each of api/chat/cron/postgres, confirm there's a `healthcheck:` block. If missing, add (api as the example):

```yaml
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      start_period: 120s
      retries: 3
```

- [ ] **Step 4: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add docker-compose.production.yml
git commit -m "ops(compose): autoheal sidecar + ahavah labels + healthchecks"
git push origin HEAD:ahavah/main
```

### Task 5.2: Deploy + verify autoheal restarts an unhealthy container

**Files:** None (deploy only).

- [ ] **Step 1: Pull + bring up**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'cd /opt/ahavah-api && git pull origin ahavah/main && \
   set -a && source .env.production && set +a && \
   docker compose -f docker-compose.yml -f docker-compose.production.yml up -d'
```

- [ ] **Step 2: Confirm autoheal is running**

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker ps --format "{{.Names}}\t{{.Status}}" | grep autoheal'
```

Expected: `autoheal Up …`.

- [ ] **Step 3: Force api into unhealthy and time the restart**

In one terminal, watch container state:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'watch -n 5 "docker ps --format \"{{.Names}}\t{{.Status}}\" | grep ahavah"'
```

In another, trigger unhealth by stopping the api's `/health` listener internally. The cleanest reproduction: kill the gunicorn parent process inside the container so the container stays "Up" but `/health` stops responding (matches the real-world scenario):

```bash
ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  'docker exec ahavah-api-api-1 sh -c "kill -SEGV 1 || kill -SEGV 7" || true'
```

- [ ] **Step 4: Time the restart**

Watch the api container status. Within ~2 minutes, it should transition:

```
ahavah-api-api-1   Up 11h (healthy)         ← before
ahavah-api-api-1   Up 11h (unhealthy)       ← worker died, healthcheck failing
ahavah-api-api-1   Restarting (0)           ← autoheal restarting
ahavah-api-api-1   Up 5 seconds (starting)  ← autoheal restarted it
ahavah-api-api-1   Up 1 minute (healthy)    ← back online
```

Verify the elapsed time between unhealthy and Up is ≤ 2.5 minutes.

- [ ] **Step 5: Milestone marker**

```bash
git commit --allow-empty -m "milestone: Phase 5 (autoheal sidecar) verified"
git push origin HEAD:ahavah/main
```

**Acceptance:** an artificially unhealthy api container is auto-restarted by autoheal within ~2 min without human intervention.

---

## Phase 6 — Handover doc

**Goal:** A handover document a fresh developer can read to understand both the observability layer they just inherited AND the broader session context (the incident that motivated this work).

### Task 6.1: Write the handover doc

**Files:**
- Create: `d:/Antigravity/ahavah-api/docs/superpowers/handovers/2026-06-09-observability-handover.md`

- [ ] **Step 1: Write the doc**

```markdown
# Observability + Admin Monitoring — Handover

**Date completed:** 2026-06-09
**Spec:** `docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md`
**Implementation plan:** `docs/superpowers/plans/2026-06-09-observability-and-admin-monitoring-implementation.md`

## Why this exists

On 2026-06-08 a gunicorn worker in `ahavah-api` SIGSEGV'd on a `UniqueViolation` raised by `Q_UNCACHED_SEARCH_2`. The race was triggered by an `ahavah-web` change (commit `7a2a65d`) that started firing concurrent `/search` calls from the same user when filters changed mid-flight. The container was marked `unhealthy` but stayed `Up`; matches / map / discover surfaces returned 502 or hung; **no one noticed for ~11 hours**.

The root SQL race is now fixed by `ahavah-api` commit `23db093` (per-user advisory lock). This work covers the orthogonal failure: nothing detected the outage, nothing alerted anyone, and nothing tried to restart the unhealthy container.

## What landed

Five phases, each independently shippable, all shipped:

| Phase | What | Files |
|---|---|---|
| 0 | UptimeRobot monitor on `/health` + Sentry project + secrets on droplet | external SaaS + `.env.production` |
| 1 | Sentry SDK in api + `system_error_log` table + Flask error handler | `service/observability/__init__.py`, `migrations/0027_system_error_log.sql` |
| 2 | `cron_run_log` table + `@cron_logged` decorator on all `*_once()` + daily prune | `service/observability/cron_logged.py`, `service/cron/prunesystemlogs/`, `migrations/0028_cron_run_log.sql` |
| 3 | Five `/admin/system/*` endpoints | `service/api/admin/system_observability_routes.py`, `service/admin/queries/system_observability.py`, `service/observability/uptimerobot.py`, `service/observability/docker_inspect.py` |
| 4 | Five System-tab tiles (Uptime, Errors, Containers, Cron, extended Health) | `ahavah-admin/src/components/admin/system/tile-*.tsx`, `src/lib/queries.ts`, `src/lib/types.ts` |
| 5 | `willfarrell/autoheal` sidecar + healthcheck labels | `docker-compose.production.yml` |

## How alerts route

```
api /health failing       → UptimeRobot       → email + SMS    ~2 min
container unhealthy       → autoheal          → silent restart ~2 min
                                              → if restart fails, UR pages
unhandled exception       → Sentry SDK        → email per new  <30s
                                                issue (deduped)
slow query > 1s           → logged only       → dashboard read
cron job crashed          → logged only       → dashboard read
```

Deliberate non-alerts: 4xx responses, individual cron failures, slow queries. They surface via the dashboard, no per-event paging.

## How to operate

### Where to look during an incident

1. Email from UptimeRobot or Sentry → click through to either's dashboard for the immediate story
2. `admin.ahavah.app` → System tab → all four new tiles + extended Health
3. SSH into droplet for raw logs:
   ```bash
   ssh -i ~/.ssh/id_ed25519_ahavah root@167.71.93.27
   docker logs ahavah-api-api-1 --tail 200
   docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api \
     -c "SELECT * FROM system_error_log ORDER BY id DESC LIMIT 20;"
   ```

### Where the secrets live

- `SENTRY_DSN` — `/opt/ahavah-api/.env.production` on droplet
- `UPTIMEROBOT_READ_API_KEY` — same file
- Both wired through to the api container via `docker-compose.production.yml` `environment:`

### How to add a new cron

1. Write your `your_thing_once()` async function as usual.
2. Decorate it: `@cron_logged('your_thing')`
3. Add `your_thing_forever()` to the `asyncio.gather` call in `service/cron/__init__.py`
4. New rows will appear in `cron_run_log` and on the dashboard Cron tile automatically.

### How to remove the system_error_log noise during dev

Set `SENTRY_DSN=''` (empty) in dev — `init_sentry()` no-ops. The `system_error_log` insertion is best-effort wrapped in `try/except` so a dev DB without the table fails silent.

### Retention

`prune_system_logs_once` runs daily at 03:00 UTC + 0-15min jitter:
- `system_error_log` rows older than 30 days are deleted
- `cron_run_log` rows older than 30 days are deleted

Override via `DUO_LOG_RETENTION_DAYS` env var if needed.

## How to verify it still works

End-to-end smoke a fresh developer can run anytime:

```bash
# 1. Force a 500 (use any endpoint that's known to throw on bad input)
curl -sk -X POST https://api.ahavah.app/decisions \
  -H 'Authorization: Bearer notvalid' -H 'Content-Type: application/json' -d '{}'

# 2. Wait 30s. Expect:
#    - email from Sentry titled "New Alert: ..."
#    - new row in system_error_log
#    - new row visible in admin.ahavah.app System tab > Errors tile

# 3. Stop the api container for 3 minutes:
ssh root@167.71.93.27 'docker stop ahavah-api-api-1'

# 4. Within ~2 min expect:
#    - email + SMS from UptimeRobot "DOWN"
#    - autoheal log: "Container Failed health check"
#    - api container "Restarting (0)"

# 5. autoheal restarts it; UR sends recovery email
```

If any of 2 / 4 fail, see the troubleshooting section.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Sentry inbox empty after forced 500 | `SENTRY_DSN` not in api container env | `docker exec ahavah-api-api-1 env \| grep SENTRY_DSN`; if missing, redeploy with the var set |
| `system_error_log` empty after forced 500 | error handler not wired | check `service/api/__init__.py` calls `install_error_handler(app)` |
| Uptime tile shows "data unavailable" | UR API key missing or rate-limited | `docker exec ahavah-api-api-1 env \| grep UPTIMEROBOT`; check UR account quota |
| Containers tile shows "data unavailable" | docker socket not mounted on api | check `docker-compose.production.yml` api service has `/var/run/docker.sock` mount |
| Cron tile shows no rows | `@cron_logged` not applied to any cron | grep `@cron_logged` in `service/cron/`; expect 12+ matches |
| autoheal not restarting | label mismatch, or `start_period` too long | `docker logs autoheal`; verify each ahavah container has `ahavah=true` label |

## Session context (the incident that motivated this work)

The full session log lives in the conversation summary; key context for a new developer:

- 2026-06-08 23:01 UTC: `ahavah-web` commit `7a2a65d` introduced concurrent `/search` calls
- ~3 hours later: first `UniqueViolation` → worker SIGSEGV → container marked `unhealthy`
- Next 11 hours: dashboard / users see broken matches/map/discover; no signal reaches anyone
- 2026-06-09 ~10:00 UTC: user noticed matches not loading and asked about it
- Root cause SQL race fixed by `ahavah-api` commit `23db093` (per-user `pg_advisory_xact_lock` in `service/search/__init__.py`)
- Observability gap closed by this work

The technical fix and the observability layer are separable; both are needed but they answer different questions ("why did it break" vs "how would we know it broke").

## What's deliberately NOT in this work

See the spec's §3 "Non-goals." Notably:
- No public status page for end users
- No Sentry performance traces (errors only)
- No log aggregation off the droplet
- No distributed tracing
- No anomaly detection
- No multi-region failover

Push back on adding any of these without a clear use case — each one is its own can of worms.

## References

- Spec: `docs/superpowers/specs/2026-06-09-observability-and-admin-monitoring-design.md`
- Plan: `docs/superpowers/plans/2026-06-09-observability-and-admin-monitoring-implementation.md`
- Admin screens spec (System tab): `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md` §Screen 9
- UptimeRobot docs: https://uptimerobot.com/api/
- Sentry Python SDK docs: https://docs.sentry.io/platforms/python/integrations/flask/
- willfarrell/autoheal: https://github.com/willfarrell/docker-autoheal
```

- [ ] **Step 2: Commit**

```bash
cd d:/Antigravity/ahavah-api
git add docs/superpowers/handovers/2026-06-09-observability-handover.md
git commit -m "docs(handover): observability + admin monitoring layer

Captures: why the work exists (2026-06-08 outage),
what landed (5 phases), how alerts route, how to operate,
how to verify, troubleshooting table, and the session
context a fresh developer needs to pick up from here."
git push origin HEAD:ahavah/main
```

- [ ] **Step 3: Final milestone marker**

```bash
git commit --allow-empty -m "milestone: observability + admin monitoring DONE"
git push origin HEAD:ahavah/main
```

---

## Self-review against the spec

Before declaring the plan done, the writer ran through the spec's components and confirmed each has at least one task:

| Spec component | Implementing task(s) |
|---|---|
| C1 — UptimeRobot monitor | Task 0.1, 0.3 |
| C2 — Sentry SDK | Task 1.2 (+ 0.2 for project, 0.3 for DSN) |
| C3 — `system_error_log` + handler | Task 1.1, 1.2, 1.3 |
| C4 — `cron_run_log` + `@cron_logged` | Task 2.1, 2.2, 2.3 |
| C5 — `/admin/system/containers` | Task 3.3, 3.4 (route), 3.6 (socket mount) |
| C6 — `/admin/system/uptime` | Task 3.2, 3.4 |
| C7 — `/admin/system/errors` + `/crons` | Task 3.1, 3.4 |
| C8 — autoheal sidecar | Task 5.1 |
| C9 — Five System-tab tiles | Tasks 4.1–4.7 |
| §6 retention prune | Task 2.4 |
| §7 alert routing — UptimeRobot + Sentry | Task 0.1, 0.2 |
| §8 deploy + verify per phase | Tasks 1.4, 2.5, 3.7, 4.8, 5.2 |

No gaps.
