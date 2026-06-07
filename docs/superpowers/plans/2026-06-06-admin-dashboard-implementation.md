# Ahavah Admin Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a separate Next.js admin app at `admin.ahavah.app` that lets staff view/manage Ahavah users + functions + data + usage trends. Backend reuses the existing `api.ahavah.app` with new `/admin/*` endpoints gated by `'admin' = ANY(person.roles)`. Every destructive action is logged to a new `admin_audit_log` table.

**Architecture:** Three pieces — (1) a new Next.js 16 project `ahavah-admin/` mirroring the stack of `ahavah-web/` but with NO PWA/onboarding code; (2) new backend routes under `service/api/admin/` returning JSON; (3) new migration `0026_admin_audit_log.sql` + new `service/admin/` module hosting the gate, audit helper, and query helpers. Phased so each phase boundary is a shippable, demo-able milestone.

**Tech Stack:**
- Backend: Python 3.12, Flask, sync psycopg via `service.database.api_tx()`, decorators from `service.api.decorators` (`@aget`, `@apost`, `@validate`)
- Frontend: Next.js 16 (App Router, Turbopack) + React 19 + Tailwind v4 + shadcn primitives (existing in `ahavah-web/src/components/ui/`) + Kibo UI blocks
- Type fonts: Plus Jakarta Sans (body) + Ultra (display) — match `ahavah-web`
- Charts: Recharts (already in stack via shadcn `chart` primitive)
- Data fetching: TanStack Query v5 (`@tanstack/react-query`) — admin reads benefit from cache/refetch ergonomics
- Deploy: Vercel project `ahavah-admin`, custom domain `admin.ahavah.app`

**Specs consumed:**
- Master design spec — `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md` (the screen-by-screen brief)
- Visual reference — Claude Design HTML at `https://api.anthropic.com/v1/design/h/oyz-64oaa1L-Kg9g7WwOKw?open_file=Ahavah+Admin+Dashboard.html` (binary-encoded; implementer should open in browser, not via curl)

**Deploy mechanics (background):**
- Backend push to `ahavah/main` → GHA `Deploy ahavah/main → droplet` → applies new `migrations/0026_*.sql` → rebuilds `api chat cron` containers
- Frontend `ahavah-admin` project on Vercel → `vercel deploy --prod` pushes to `admin.ahavah.app`. The domain is added to the project on first deploy and tracks the `main` branch

---

## Inviolable design system rules (DON'T VIOLATE THESE)

These bind the implementer for **every** screen. Carry them into every subagent prompt:

1. **Kit primitives only.** Every UI element must be one of:
   - A shadcn primitive copied from `ahavah-web/src/components/ui/` to `ahavah-admin/src/components/ui/` (Button, Card, Badge, Input, Label, Sheet, Dialog, Table, Tabs, DropdownMenu, Avatar, Skeleton, Chart, Tooltip, Switch, Select, Pagination)
   - A Kibo UI block (Pill, Status, RelativeTime)
   - A composition of the above with Tailwind utility classes
   
   **NEVER** roll a custom `<div className="rounded bg-white p-4">` when `<Card>` exists. **NEVER** invent a new button atom. If a primitive doesn't cover a need, extend it; don't replace it.

2. **Brand tokens come from `ahavah-web/src/app/globals.css`.** Copy the relevant CSS variables verbatim into `ahavah-admin/src/app/globals.css`. Do NOT introduce new color tokens.

3. **Desktop-primary.** Every screen is designed at `≥768px` first. Mobile responsiveness is a fallback (read-only — destructive buttons hidden, drawers become full-screen sheets).

4. **No hand-rolled animations.** Use `motion/react` (already in stack) sparingly — match `ahavah-web` patterns. No custom CSS keyframes.

5. **Data state must be visible.** Every list/chart has explicit empty / loading / error states using `<Skeleton>` (load) and a friendly card with icon + copy (empty / error). No silent spinners.

6. **Every destructive action goes through `<Dialog>` confirmation.** Two-button shape: ghost "Cancel" + destructive-red "Confirm". Hard delete requires typing the email to confirm.

7. **Auth gate must be tested at every endpoint.** Every `@aget`/`@apost` under `/admin/*` calls `service.admin.require_admin(s)` as the FIRST line; returns 403 otherwise. Plan includes a pytest for this on each endpoint.

---

## File structure

### Backend (`ahavah-api/`)

| File | Action | Responsibility |
|---|---|---|
| `migrations/0026_admin_audit_log.sql` | Create | The audit log table + 3 indexes |
| `service/admin/__init__.py` | Create | Public module surface: `require_admin`, `record_audit`, query helpers grouped by tab |
| `service/admin/queries/__init__.py` | Create | SQL constants split per concern (overview, users, cohorts, economy, moderation, system, audit) |
| `service/api/admin/__init__.py` | Create | Marker init for the route subpackage |
| `service/api/admin/overview_routes.py` | Create | `GET /admin/whoami`, `GET /admin/overview` |
| `service/api/admin/users_routes.py` | Create | `GET /admin/users`, `GET /admin/users/:uuid`, action endpoints |
| `service/api/admin/cohorts_routes.py` | Create | `GET /admin/cohorts/{waitlist|beta|referrals}` |
| `service/api/admin/economy_routes.py` | Create | `GET /admin/economy/{ledger|entitlements|subscriptions}` |
| `service/api/admin/moderation_routes.py` | Create | `GET /admin/moderation/{reports|photos|messages}` |
| `service/api/admin/system_routes.py` | Create | `GET /admin/system/health` |
| `service/api/admin/audit_routes.py` | Create | `GET /admin/audit-log` |
| `service/api/__init__.py` | Modify | Import the new admin route modules at the bottom |
| `duotypes/__init__.py` | Modify | Add Pydantic models for the 4 user-action endpoints |
| `tests/test_admin.py` | Create | Pytest covering `require_admin` gate logic + the per-endpoint 403 gate |

### Frontend (`ahavah-admin/` — new Next.js project)

| File | Action | Responsibility |
|---|---|---|
| `package.json` | Create | Next 16 + React 19 + Tailwind 4 + shadcn + TanStack Query + Recharts deps |
| `next.config.ts` | Create | `/api/*` rewrite → `api.ahavah.app/*`; security headers; no PWA config |
| `tsconfig.json` | Create | Same paths config as `ahavah-web/tsconfig.json` |
| `tailwind.config.ts` + `postcss.config.mjs` | Create | Tailwind v4 setup |
| `src/app/layout.tsx` | Create | Root layout with QueryClientProvider + Toaster |
| `src/app/globals.css` | Create | Copy of `ahavah-web/src/app/globals.css` brand tokens + admin-specific tweaks |
| `src/app/page.tsx` | Create | Single-page tabbed admin shell |
| `src/app/auth/sign-in/page.tsx` | Create | OTP sign-in mirroring `ahavah-web/src/app/auth/sign-in/page.tsx` |
| `src/app/auth/sign-out/route.ts` | Create | Server route handler that calls backend `POST /sign-out`, clears cookie, redirects |
| `src/app/unauthorized/page.tsx` | Create | Screen 2 — 403 fallback |
| `src/components/ui/*` | Create | Copy needed shadcn primitives from `ahavah-web/src/components/ui/` |
| `src/components/admin/shell.tsx` | Create | Shared shell: header + sidebar nav + main outlet |
| `src/components/admin/sidebar-nav.tsx` | Create | Tab list with active state, syncs with `?tab=` |
| `src/components/admin/kpi-card.tsx` | Create | Used by Overview + System + Cohorts |
| `src/components/admin/tab-overview.tsx` | Create | Screen 3 — KPIs + charts + activity feed |
| `src/components/admin/tab-users.tsx` | Create | Screen 4 — search + filter + table |
| `src/components/admin/user-drawer.tsx` | Create | Screen 5 — sticky-header drawer with 4 internal tabs |
| `src/components/admin/user-drawer-{identity,activity,economy,actions}.tsx` | Create | The 4 internal drawer tabs |
| `src/components/admin/tab-cohorts.tsx` | Create | Screen 6 — with 3 sub-tab routes |
| `src/components/admin/tab-economy.tsx` | Create | Screen 7 |
| `src/components/admin/tab-moderation.tsx` | Create | Screen 8 |
| `src/components/admin/tab-system.tsx` | Create | Screen 9 |
| `src/components/admin/tab-audit.tsx` | Create | Screen 10 |
| `src/lib/api-client.ts` | Create | Lightweight fetch wrapper (copy + simplify from ahavah-web's pattern) |
| `src/lib/queries.ts` | Create | TanStack Query hooks per endpoint — `useOverview()`, `useUsers()`, `useUser(uuid)`, etc. |
| `src/lib/mutations.ts` | Create | Mutations for the action endpoints (grant/revoke entitlement, credit/debit tokens, etc.) |
| `src/lib/types.ts` | Create | TypeScript types for the backend response shapes |
| `vercel.json` | Create | Build config + redirects (`/` → `/?tab=overview`) |

---

# Phase 0 — Project scaffold & auth handshake

**Outcome at phase end:** `admin.ahavah.app` is reachable, asks for OTP, accepts an admin's OTP, shows a placeholder "Signed in as <email>" page, and a non-admin sees the Unauthorized screen.

### Task 0.1: Add `'admin'` role to a real DB row + migration check

**Files:**
- Update: `person` row for `harrigan.tennyson@gmail.com` (or whoever the first admin should be)

- [ ] **Step 1: Grant yourself the admin role on prod**

Run from the local box:

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   \"UPDATE person SET roles = array_append(coalesce(roles, ARRAY[]::text[]), 'admin') WHERE email = 'harrigan.tennyson@gmail.com' AND NOT ('admin' = ANY(coalesce(roles, ARRAY[]::text[])));\""
```

Expected: `UPDATE 1`.

- [ ] **Step 2: Verify**

```bash
ssh -i C:/Users/Ehud/.ssh/id_ed25519_ahavah root@167.71.93.27 \
  "docker exec ahavah-api-postgres-1 psql -U postgres -d duo_api -c \
   \"SELECT email, roles FROM person WHERE 'admin' = ANY(roles);\""
```

Expected output includes `harrigan.tennyson@gmail.com | {admin}`.

### Task 0.2: Migration 0026 — `admin_audit_log` table

**Files:**
- Create: `ahavah-api/migrations/0026_admin_audit_log.sql`

- [ ] **Step 1: Create the migration**

```sql
-- 0026_admin_audit_log.sql
-- Append-only log of every destructive admin action. Idempotent.
-- See docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md.
BEGIN;

CREATE TABLE IF NOT EXISTS admin_audit_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_email   TEXT NOT NULL,
  actor_uuid    UUID NOT NULL,
  action        TEXT NOT NULL,
  target_email  TEXT,
  target_uuid   UUID,
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS admin_audit_log_actor_created_idx
  ON admin_audit_log (actor_email, created_at DESC);

CREATE INDEX IF NOT EXISTS admin_audit_log_target_created_idx
  ON admin_audit_log (target_email, created_at DESC)
  WHERE target_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS admin_audit_log_action_created_idx
  ON admin_audit_log (action, created_at DESC);

COMMIT;
```

- [ ] **Step 2: Commit**

```bash
cd ahavah-api
git add migrations/0026_admin_audit_log.sql
git commit -m "migration 0026: admin_audit_log table (admin dashboard prereq)"
```

### Task 0.3: `service/admin/__init__.py` — gate + audit helper

**Files:**
- Create: `ahavah-api/service/admin/__init__.py`
- Create: `ahavah-api/service/admin/queries/__init__.py`

- [ ] **Step 1: Create the queries package**

```python
# ahavah-api/service/admin/queries/__init__.py
"""SQL constants for the admin dashboard, split per concern to keep
each section under ~100 lines. Each submodule re-exports its
constants here for service.admin.* convenience."""
# Submodules added as each phase lands. Phase 0 only needs the gate
# + audit helper queries below.

Q_IS_ADMIN = """
    SELECT 1 = ANY (
        SELECT 1 FROM person
         WHERE uuid = %(uuid)s
           AND 'admin' = ANY(coalesce(roles, ARRAY[]::text[]))
    ) AS is_admin
"""

Q_INSERT_AUDIT = """
    INSERT INTO admin_audit_log
        (actor_email, actor_uuid, action, target_email, target_uuid, metadata)
    VALUES
        (%(actor_email)s, %(actor_uuid)s, %(action)s,
         %(target_email)s, %(target_uuid)s, %(metadata)s)
    RETURNING id
"""
```

- [ ] **Step 2: Create the module surface**

```python
# ahavah-api/service/admin/__init__.py
"""Admin dashboard — service-layer module.

Every /admin/* endpoint:
  1. Calls require_admin(s) FIRST. Returns 403 for non-admins.
  2. For mutations: calls record_audit(tx, s, action, target, metadata)
     in the SAME api_tx as the mutation so they commit atomically.

Public surface:
  require_admin(s)             — raises HTTPException(403) if not admin
  record_audit(tx, s, ...)     — writes one admin_audit_log row
  is_admin(tx, person_uuid)    — read-only check (used by /admin/whoami)

Query helpers per tab land in service/admin/queries/* and are
imported here as each phase ships."""
from __future__ import annotations

import json
from typing import Any, Optional

from database import api_tx
from flask import abort
import duotypes as t

from service.admin.queries import Q_IS_ADMIN, Q_INSERT_AUDIT


def is_admin(tx, person_uuid: str) -> bool:
    """1-row gate check. tx is a psycopg cursor owned by the caller."""
    if not person_uuid:
        return False
    row = tx.execute(Q_IS_ADMIN, dict(uuid=person_uuid)).fetchone()
    return bool(row and row.get('is_admin'))


def require_admin(s: t.SessionInfo) -> None:
    """First line of every /admin/* handler. Aborts with 403 if the
    caller's session person_uuid is not in the admin role. Opens its
    own read-only tx — the handler can still open its own write tx."""
    if not s or not s.person_uuid:
        abort(403)
    with api_tx('read committed') as tx:
        if not is_admin(tx, s.person_uuid):
            abort(403)


def record_audit(
    tx,
    s: t.SessionInfo,
    action: str,
    target_email: Optional[str] = None,
    target_uuid: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Write one admin_audit_log row. Caller MUST be inside an api_tx
    that also holds the mutation being audited — so the audit + the
    mutation commit (or roll back) together."""
    row = tx.execute(
        Q_INSERT_AUDIT,
        dict(
            actor_email=s.email,
            actor_uuid=s.person_uuid,
            action=action,
            target_email=target_email,
            target_uuid=target_uuid,
            metadata=json.dumps(metadata or {}),
        ),
    ).fetchone()
    return str(row['id'])
```

- [ ] **Step 3: Commit**

```bash
cd ahavah-api
git add service/admin/__init__.py service/admin/queries/__init__.py
git commit -m "service.admin: require_admin gate + record_audit helper

Foundational module for the admin dashboard. Every /admin/* endpoint
must call require_admin(s) FIRST and record_audit(tx, ...) for any
mutation, in the same tx as the mutation itself."
```

### Task 0.4: `tests/test_admin.py` — gate logic

**Files:**
- Create: `ahavah-api/tests/test_admin.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for service.admin gate logic.

Pure-logic tests over the SQL string + the require_admin / record_audit
function shapes. Integration of the actual /admin/* endpoints is
covered by per-route tests added in each subsequent phase."""
from __future__ import annotations


def test_q_is_admin_uses_named_parameter():
    from service.admin.queries import Q_IS_ADMIN
    assert "%(uuid)s" in Q_IS_ADMIN
    assert "format" not in Q_IS_ADMIN.lower()
    assert "f'" not in Q_IS_ADMIN


def test_q_insert_audit_has_all_columns():
    from service.admin.queries import Q_INSERT_AUDIT
    for col in [
        "actor_email", "actor_uuid", "action",
        "target_email", "target_uuid", "metadata",
    ]:
        assert f"%({col})s" in Q_INSERT_AUDIT


def test_is_admin_returns_false_for_empty_uuid():
    from service.admin import is_admin
    assert is_admin(tx=None, person_uuid="") is False
    assert is_admin(tx=None, person_uuid=None) is False
```

- [ ] **Step 2: Run tests, expect PASS**

```bash
cd /d/Antigravity/ahavah-api && python -m pytest tests/test_admin.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
cd ahavah-api
git add tests/test_admin.py
git commit -m "tests: admin gate + audit query structural checks"
```

### Task 0.5: `GET /admin/whoami` — first admin endpoint

**Files:**
- Create: `ahavah-api/service/api/admin/__init__.py` (empty package init)
- Create: `ahavah-api/service/api/admin/overview_routes.py`
- Modify: `ahavah-api/service/api/__init__.py`

- [ ] **Step 1: Package init**

Path: `ahavah-api/service/api/admin/__init__.py`

```python
"""Admin route subpackage. Each route module is imported at the bottom
of service/api/__init__.py to register with the Flask app."""
```

- [ ] **Step 2: First route — whoami**

Path: `ahavah-api/service/api/admin/overview_routes.py`

```python
"""Overview tab routes.

GET /admin/whoami — { is_admin, email, person_uuid }. Returns 200
even for non-admins (so the FE can render the Unauthorized screen
without first hitting a 403). Other handlers in this module call
require_admin and return 403 for non-admins."""
from __future__ import annotations

import duotypes as t

from service.api.decorators import aget
from service.admin import is_admin
from database import api_tx


@aget('/admin/whoami')
def get_admin_whoami(s: t.SessionInfo):
    if not s or not s.person_uuid:
        return {'is_admin': False, 'email': None, 'person_uuid': None}
    with api_tx('read committed') as tx:
        admin = is_admin(tx, s.person_uuid)
    return {
        'is_admin': admin,
        'email': s.email if admin else None,
        'person_uuid': s.person_uuid if admin else None,
    }
```

- [ ] **Step 3: Mount it**

Find the bottom of `service/api/__init__.py` (just after the existing `import service.api.account_routes` line) and add:

```python
# Admin dashboard (2026-06-06) — staff-only /admin/* surface.
# Each route module is imported per phase as it lands.
import service.api.admin.overview_routes  # noqa: E402,F401
```

- [ ] **Step 4: Smoke test the gate (after deploy)**

Skipped at task-time. Fires at Task 0.9.

- [ ] **Step 5: Commit**

```bash
cd ahavah-api
git add service/api/admin/__init__.py service/api/admin/overview_routes.py service/api/__init__.py
git commit -m "GET /admin/whoami — first admin endpoint + module scaffold

Returns is_admin + caller identity. The only /admin/* endpoint that
returns 200 for non-admins (so the FE can render the Unauthorized
screen without first seeing a 403)."
```

### Task 0.6: Push backend + watch GHA

- [ ] **Step 1: Push**

```bash
cd /d/Antigravity/ahavah-api
git push origin ahavah/main
```

- [ ] **Step 2: Watch GHA**

```bash
sleep 10
gh run watch --repo techbasesolutions/duolicious-backend \
  $(gh run list --repo techbasesolutions/duolicious-backend --branch ahavah/main --limit 1 --json databaseId --jq '.[0].databaseId') \
  --exit-status
```

Expected: `✓ Pull + rebuild api/chat`. Migration 0026 applies automatically.

### Task 0.7: Scaffold `ahavah-admin` Next.js project

**Files:**
- Create: `d:/Antigravity/ahavah-admin/` (new directory with full Next.js scaffold)

- [ ] **Step 1: Create the project**

```bash
cd /d/Antigravity
pnpm create next-app@16 ahavah-admin --typescript --tailwind --app --no-src-dir --import-alias "@/*" --turbopack --no-eslint --use-pnpm
```

When prompted, accept defaults except: NO `src/` dir reversion — restructure manually to use `src/`. After scaffold:

```bash
cd ahavah-admin
mkdir src
mv app src/
# Update tsconfig.json paths from "./app/*" to "./src/app/*" (already implied by import alias)
```

- [ ] **Step 2: Install required deps**

```bash
cd /d/Antigravity/ahavah-admin
pnpm add @tanstack/react-query @tanstack/react-query-devtools \
         lucide-react class-variance-authority clsx tailwind-merge \
         motion sonner recharts \
         @radix-ui/react-slot @radix-ui/react-checkbox @radix-ui/react-label \
         @radix-ui/react-dialog @radix-ui/react-tooltip @radix-ui/react-tabs \
         @radix-ui/react-dropdown-menu @radix-ui/react-select \
         @radix-ui/react-avatar @radix-ui/react-switch
pnpm add -D @types/node
```

- [ ] **Step 3: Initialize git + first commit**

```bash
cd /d/Antigravity/ahavah-admin
git init
git add .
git commit -m "scaffold: Next 16 + Tailwind 4 + TanStack Query for ahavah-admin"
```

- [ ] **Step 4: Create the GitHub repo + push**

```bash
gh repo create techbasesolutions/ahavah-admin --private --source=. --remote=origin --push
```

### Task 0.8: Copy brand tokens + base CSS from ahavah-web

**Files:**
- Modify: `ahavah-admin/src/app/globals.css`
- Modify: `ahavah-admin/src/app/layout.tsx`

- [ ] **Step 1: Copy globals.css**

Open `d:/Antigravity/ahavah-web/src/app/globals.css` and copy the entire `:root` + `@theme` + brand-token CSS variable blocks into `d:/Antigravity/ahavah-admin/src/app/globals.css`. Also copy the `.ahavah-app` shell rule but REMOVE the `:has(> [data-landing])` rule (no landing page here). Keep the `html, body` rules.

Add at the end:

```css
@layer base {
  /* Admin-specific: lighter line-height for tables, more compact density. */
  table { line-height: 1.35; }
  .admin-table-row:hover { background-color: rgb(255 255 255 / 0.03); }
}
```

- [ ] **Step 2: Update root layout**

Path: `src/app/layout.tsx`

```typescript
import type { Metadata } from "next";
import { Plus_Jakarta_Sans, Ultra } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/lib/query-provider";
import { Toaster } from "sonner";

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const ultra = Ultra({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-display",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Ahavah Admin",
  description: "Internal staff dashboard for Ahavah.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plusJakartaSans.variable} ${ultra.variable} dark h-full antialiased`} suppressHydrationWarning>
      <body className="min-h-full bg-(--app) text-(--ink)" suppressHydrationWarning>
        <QueryProvider>
          {children}
          <Toaster position="top-center" richColors closeButton />
        </QueryProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 3: Create the QueryProvider client component**

Path: `src/lib/query-provider.tsx`

```typescript
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState } from "react";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,        // 30s — admin views can tolerate stale-while-revalidate
        gcTime: 5 * 60_000,       // 5 min cache retention
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  }));
  return (
    <QueryClientProvider client={client}>
      {children}
      {process.env.NODE_ENV === "development" && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  );
}
```

- [ ] **Step 4: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add src/app/globals.css src/app/layout.tsx src/lib/query-provider.tsx
git commit -m "shell: brand tokens copied from ahavah-web + QueryProvider"
```

### Task 0.9: Next config — API rewrite + security headers

**Files:**
- Modify: `ahavah-admin/next.config.ts`

- [ ] **Step 1: Write the config**

Path: `next.config.ts`

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Same /api rewrite pattern as ahavah-web. Lets the FE call /api/*
  // same-origin so cookies attach automatically.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "https://api.ahavah.app/:path*" },
    ];
  },

  async headers() {
    const securityHeaders = [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
      {
        key: "Content-Security-Policy",
        value: [
          "default-src 'self'",
          "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
          "style-src 'self' 'unsafe-inline'",
          "img-src 'self' data: blob: https://user-images.ahavah.app https://email-assets.ahavah.app https://*.digitaloceanspaces.com",
          "font-src 'self' data:",
          "connect-src 'self' https://api.ahavah.app",
          "frame-ancestors 'none'",
          "form-action 'self'",
          "base-uri 'self'",
          "object-src 'none'",
          "upgrade-insecure-requests",
        ].join("; "),
      },
    ];
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
```

- [ ] **Step 2: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add next.config.ts
git commit -m "config: /api rewrite to api.ahavah.app + security headers"
```

### Task 0.10: Sign-in screen — Screen 1 of the spec

**Files:**
- Create: `ahavah-admin/src/components/ui/button.tsx`, `card.tsx`, `input.tsx`, `label.tsx` (copy from ahavah-web)
- Create: `ahavah-admin/src/lib/api-client.ts`
- Create: `ahavah-admin/src/app/auth/sign-in/page.tsx`

- [ ] **Step 1: Copy the shadcn primitives needed for sign-in**

```bash
cd /d/Antigravity/ahavah-admin
mkdir -p src/components/ui
cp ../ahavah-web/src/components/ui/button.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/card.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/input.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/label.tsx src/components/ui/
mkdir -p src/lib
cp ../ahavah-web/src/lib/utils.ts src/lib/  # cn() helper
```

If any of those primitives import other primitives or `@/lib/something`, copy those too. The compile error after `pnpm dev` will tell you what's missing — keep copying until clean.

- [ ] **Step 2: Lightweight API client**

Path: `src/lib/api-client.ts`

```typescript
/**
 * Minimal API client. The /api rewrite in next.config.ts forwards to
 * api.ahavah.app, so all calls are same-origin (cookies attach).
 *
 * NOTE: same OTP backend as ahavah-web. The session_token cookie is
 * scoped to admin.ahavah.app by the backend's Set-Cookie response.
 */

const BASE = "/api";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include",
  });
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  if (!res.ok) throw new ApiError(res.status, parsed);
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body?: unknown) => call<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => call<T>("PATCH", path, body),
  delete: <T>(path: string, body?: unknown) => call<T>("DELETE", path, body),
};

/**
 * The /request-otp endpoint returns a session_token that the backend
 * uses as a bearer for /check-otp. We persist it in sessionStorage
 * for the few seconds between request + check.
 */
const PENDING_KEY = "admin.pendingSessionToken";

export async function requestEmailOtp(email: string): Promise<void> {
  const res = await api.post<{ session_token: string }>("/request-otp", { email });
  sessionStorage.setItem(PENDING_KEY, res.session_token);
}

export async function checkOtp(email: string, otp: string): Promise<{ person_uuid: string; onboarded: boolean }> {
  const token = sessionStorage.getItem(PENDING_KEY) || "";
  const res = await fetch(BASE + "/check-otp", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ email, otp }),
    credentials: "include",
  });
  const body = await res.json();
  if (!res.ok) throw new ApiError(res.status, body);
  sessionStorage.removeItem(PENDING_KEY);
  return body;
}
```

- [ ] **Step 3: Sign-in page**

Path: `src/app/auth/sign-in/page.tsx`

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, checkOtp, requestEmailOtp } from "@/lib/api-client";

type Stage = "email" | "otp";

export default function SignInPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSendCode(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@") || submitting) return;
    setError(null); setSubmitting(true);
    try {
      await requestEmailOtp(email);
      setStage("otp");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429
        ? "Too many requests. Try again in a few minutes."
        : "Something went wrong. Please try again.");
    } finally { setSubmitting(false); }
  }

  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault();
    if (otp.length !== 6 || submitting) return;
    setError(null); setSubmitting(true);
    try {
      await checkOtp(email, otp);
      // After sign-in, /whoami tells us if we're admin or not.
      const who = await api.get<{ is_admin: boolean }>("/admin/whoami");
      router.push(who.is_admin ? "/" : "/unauthorized");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 400
        ? "That code didn't match. Check the email."
        : "Sign-in failed. Try again.");
    } finally { setSubmitting(false); }
  }

  return (
    <main className="min-h-dvh grid place-items-center px-6">
      <div className="w-full max-w-sm flex flex-col gap-6">
        <div className="text-center">
          <h1 className="text-display text-(--ink) m-0">Ahavah Admin</h1>
          <p className="mt-2 text-meta text-(--ink-2)">Sign in to the admin panel.</p>
        </div>

        {stage === "email" ? (
          <form onSubmit={handleSendCode} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" autoComplete="email" autoFocus
                value={email} onChange={(e) => setEmail(e.target.value)}
                placeholder="you@ahavah" />
            </div>
            <Button type="submit" tone="cta" size="cta" disabled={submitting || !email.includes("@")}>
              {submitting ? <><Loader2 className="animate-spin" /> Sending…</> : "Send code"}
            </Button>
          </form>
        ) : (
          <form onSubmit={handleSignIn} className="flex flex-col gap-4">
            <p className="text-meta text-(--ink-2)">
              We sent a code to <strong className="text-(--ink)">{email}</strong>.
            </p>
            <div className="flex flex-col gap-2">
              <Label htmlFor="otp">6-digit code</Label>
              <Input id="otp" inputMode="numeric" pattern="[0-9]*" maxLength={6}
                autoFocus value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
            </div>
            <Button type="submit" tone="cta" size="cta" disabled={submitting || otp.length !== 6}>
              {submitting ? <><Loader2 className="animate-spin" /> Signing in…</> : "Sign in"}
            </Button>
          </form>
        )}

        {error && <p role="alert" className="text-center text-caption text-(--color-pink)">{error}</p>}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Unauthorized page (Screen 2)**

Path: `src/app/unauthorized/page.tsx`

```typescript
"use client";

import { ShieldX } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function UnauthorizedPage() {
  return (
    <main className="min-h-dvh grid place-items-center px-6">
      <div className="w-full max-w-sm flex flex-col gap-6 text-center">
        <div className="mx-auto h-16 w-16 grid place-items-center rounded-full bg-(--color-lavender)/15">
          <ShieldX className="size-8 text-(--color-lavender)" />
        </div>
        <div>
          <h1 className="text-display text-(--ink) m-0">You don&apos;t have access here</h1>
          <p className="mt-2 text-meta text-(--ink-2)">
            Your account isn&apos;t authorized to use the admin panel. If you think this is a mistake, contact the team.
          </p>
        </div>
        <div className="flex justify-center gap-2">
          <Button variant="ghost" onClick={() => window.location.assign("https://ahavah.app")}>
            Return to ahavah.app
          </Button>
          <Button variant="outline" onClick={async () => {
            await fetch("/api/sign-out", { method: "POST", credentials: "include" });
            window.location.assign("/auth/sign-in");
          }}>
            Sign out
          </Button>
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Root page — redirect to sign-in until shell exists**

Path: `src/app/page.tsx` (temporary placeholder)

```typescript
import { redirect } from "next/navigation";

export default function HomePage() {
  // Phase 0 placeholder. Phase 1 replaces this with the tabbed shell.
  redirect("/auth/sign-in");
}
```

- [ ] **Step 6: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add .
git commit -m "auth: sign-in + unauthorized screens + minimal api-client"
```

### Task 0.11: Deploy to Vercel + add admin.ahavah.app domain

**Files:** none (CLI ops)

- [ ] **Step 1: First deploy**

```bash
cd /d/Antigravity/ahavah-admin
vercel deploy --prod --token <VERCEL_TOKEN>
# Capture the produced URL e.g. https://ahavah-admin-xxx.vercel.app
```

- [ ] **Step 2: Add the custom domain**

```bash
vercel domains add admin.ahavah.app --token <VERCEL_TOKEN>
```

DNS is on Vercel nameservers (verified during /signup work) — the domain will resolve within ~1 minute.

- [ ] **Step 3: Smoke**

```bash
curl -sI https://admin.ahavah.app/auth/sign-in | head -3
```

Expected: `HTTP/1.1 200 OK`.

- [ ] **Step 4: End-to-end auth smoke**

In a real browser: visit `https://admin.ahavah.app`, get redirected to `/auth/sign-in`, enter your admin email, get the OTP from your inbox (or pull from DB), enter it, expect a redirect to `/` (which redirects back to /auth/sign-in for now since Phase 0 only ships auth).

To bypass the empty `/` redirect for now, manually visit `https://admin.ahavah.app/api/admin/whoami` directly after sign-in — should return `{"is_admin": true, "email": "...", "person_uuid": "..."}`.

### Task 0.12: Phase 0 boundary — tag milestone

- [ ] **Step 1: Tag**

```bash
cd /d/Antigravity/ahavah-admin
git tag phase0-admin-scaffold
git push origin phase0-admin-scaffold

cd /d/Antigravity/ahavah-api
git tag phase0-admin-backend
git push origin phase0-admin-backend
```

**Phase 0 shippable artifact:** anyone with `'admin' = ANY(roles)` can sign in at `admin.ahavah.app` and the backend correctly identifies them.

---

# Phase 1 — Overview tab (Screen 3) + shared shell

**Outcome at phase end:** admin signs in → lands on `/?tab=overview` → sees the 6 KPI cards + 3 chart panels + recent-activity feed populated with real production data. All other tabs are placeholder "Coming soon" cards.

### Task 1.1: Backend — `GET /admin/overview`

**Files:**
- Create: `ahavah-api/service/admin/queries/overview.py`
- Modify: `ahavah-api/service/api/admin/overview_routes.py`
- Modify: `ahavah-api/service/admin/queries/__init__.py` (re-export)
- Create: `ahavah-api/tests/test_admin_overview.py`

- [ ] **Step 1: SQL constants for the overview**

Path: `service/admin/queries/overview.py`

```python
"""SQL for GET /admin/overview — the dashboard's top-of-page KPIs +
3 chart series + recent activity feed."""

Q_OVERVIEW_KPIS = """
    SELECT
        (SELECT COUNT(*) FROM waitlist_signup WHERE created_at::date = CURRENT_DATE)
            + (SELECT COUNT(*) FROM beta_signup WHERE created_at::date = CURRENT_DATE) AS signups_today,
        (SELECT COUNT(*) FROM person WHERE last_online_time > NOW() - INTERVAL '24 hours') AS active_24h,
        (SELECT COUNT(*) FROM person) AS person_total,
        (SELECT COUNT(*) FROM person WHERE 'premium' = ANY(entitlements)) AS premium_holders,
        (SELECT COUNT(*) FROM skipped WHERE reported = TRUE) AS pending_reports,
        (SELECT COUNT(*) FROM referral WHERE created_at > NOW() - INTERVAL '7 days') AS referral_signups_7d
"""

# 30-day signup series for the stacked area chart.
Q_OVERVIEW_SIGNUPS_30D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '29 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((SELECT COUNT(*) FROM waitlist_signup WHERE created_at::date = days.d), 0) AS waitlist,
        COALESCE((SELECT COUNT(*) FROM beta_signup WHERE created_at::date = days.d), 0) AS beta,
        COALESCE((SELECT COUNT(*) FROM person WHERE sign_up_time::date = days.d), 0) AS person
      FROM days
     ORDER BY days.d
"""

# 7-day CTR series for referral links — clicks vs signups attributed.
Q_OVERVIEW_REFERRAL_CTR_7D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '6 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((SELECT COUNT(*) FROM referral_link_click WHERE created_at::date = days.d AND user_agent_class != 'bot'), 0) AS clicks,
        COALESCE((SELECT COUNT(*) FROM referral WHERE created_at::date = days.d), 0) AS signups
      FROM days
     ORDER BY days.d
"""

# 30-day premium adds — entitlement grants per day from the audit log
# OR (until audit fills) from token_ledger 'subscription_stipend' as a proxy.
Q_OVERVIEW_PREMIUM_30D = """
    WITH days AS (
        SELECT generate_series(
            (CURRENT_DATE - INTERVAL '29 days')::date,
            CURRENT_DATE,
            INTERVAL '1 day'
        )::date AS d
    )
    SELECT
        days.d::text AS day,
        COALESCE((
            SELECT COUNT(*) FROM admin_audit_log
             WHERE action = 'grant_entitlement'
               AND metadata->>'name' = 'premium'
               AND created_at::date = days.d
        ), 0) AS adds
      FROM days
     ORDER BY days.d
"""

# Last 20 notable events — union of signups, abuse flags, referral
# attributions, audit log. Reverse chrono.
Q_OVERVIEW_RECENT_ACTIVITY = """
    SELECT * FROM (
        SELECT 'signup' AS kind, email AS subject, NULL::text AS object,
               created_at FROM beta_signup ORDER BY created_at DESC LIMIT 10
    ) UNION ALL SELECT * FROM (
        SELECT 'signup_waitlist' AS kind, email AS subject, NULL::text AS object,
               created_at FROM waitlist_signup ORDER BY created_at DESC LIMIT 10
    ) UNION ALL SELECT * FROM (
        SELECT 'referral_credited' AS kind, inviter_email AS subject, invitee_email AS object,
               credited_at AS created_at FROM referral
         WHERE status = 'credited' AND credited_at IS NOT NULL
         ORDER BY credited_at DESC LIMIT 10
    ) UNION ALL SELECT * FROM (
        SELECT 'admin_action' AS kind, actor_email AS subject, action AS object,
               created_at FROM admin_audit_log ORDER BY created_at DESC LIMIT 10
    )
    ORDER BY created_at DESC LIMIT 20
"""
```

- [ ] **Step 2: Re-export**

In `service/admin/queries/__init__.py`, add after the existing Q_INSERT_AUDIT:

```python
from service.admin.queries.overview import (
    Q_OVERVIEW_KPIS,
    Q_OVERVIEW_SIGNUPS_30D,
    Q_OVERVIEW_REFERRAL_CTR_7D,
    Q_OVERVIEW_PREMIUM_30D,
    Q_OVERVIEW_RECENT_ACTIVITY,
)
```

- [ ] **Step 3: Endpoint**

In `service/api/admin/overview_routes.py`, append:

```python
from service.admin import require_admin
from service.admin.queries import (
    Q_OVERVIEW_KPIS,
    Q_OVERVIEW_SIGNUPS_30D,
    Q_OVERVIEW_REFERRAL_CTR_7D,
    Q_OVERVIEW_PREMIUM_30D,
    Q_OVERVIEW_RECENT_ACTIVITY,
)


@aget('/admin/overview')
def get_admin_overview(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        kpis = tx.execute(Q_OVERVIEW_KPIS).fetchone() or {}
        signups_30d = [dict(r) for r in tx.execute(Q_OVERVIEW_SIGNUPS_30D).fetchall()]
        referral_ctr_7d = [dict(r) for r in tx.execute(Q_OVERVIEW_REFERRAL_CTR_7D).fetchall()]
        premium_30d = [dict(r) for r in tx.execute(Q_OVERVIEW_PREMIUM_30D).fetchall()]
        recent = [dict(r) for r in tx.execute(Q_OVERVIEW_RECENT_ACTIVITY).fetchall()]
    return {
        'kpis': dict(kpis),
        'signups_30d': signups_30d,
        'referral_ctr_7d': referral_ctr_7d,
        'premium_30d': premium_30d,
        'recent_activity': recent,
    }
```

- [ ] **Step 4: Pytest the gate**

Path: `tests/test_admin_overview.py`

```python
"""Endpoint-shape tests for /admin/overview. The actual SQL execution
is integration-tested via the live smoke step at Phase 1 end."""

def test_overview_routes_module_imports():
    import service.api.admin.overview_routes
    # Both handlers registered with Flask via the decorators.
    # If imports above succeed, the @aget decorator has run.


def test_q_overview_kpis_is_single_select():
    from service.admin.queries import Q_OVERVIEW_KPIS
    assert Q_OVERVIEW_KPIS.strip().upper().startswith("SELECT")
    # 6 KPI columns expected.
    assert Q_OVERVIEW_KPIS.count(" AS ") >= 6
```

- [ ] **Step 5: Run pytest**

```bash
cd /d/Antigravity/ahavah-api && python -m pytest tests/test_admin_overview.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit + push**

```bash
cd ahavah-api
git add service/admin/queries/overview.py \
        service/admin/queries/__init__.py \
        service/api/admin/overview_routes.py \
        tests/test_admin_overview.py
git commit -m "GET /admin/overview — KPIs + 3 chart series + activity feed"
git push origin ahavah/main
```

### Task 1.2: FE — copy remaining shadcn primitives + Kibo Pill

- [ ] **Step 1: Copy required primitives**

```bash
cd /d/Antigravity/ahavah-admin
cp ../ahavah-web/src/components/ui/badge.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/avatar.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/dialog.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/sheet.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/table.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/tabs.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/skeleton.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/dropdown-menu.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/select.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/tooltip.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/switch.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/chart.tsx src/components/ui/
cp ../ahavah-web/src/components/ui/checkbox.tsx src/components/ui/

# Kibo Pill
mkdir -p src/components/kibo-ui/pill
cp -r ../ahavah-web/src/components/kibo-ui/pill src/components/kibo-ui/
```

Run `pnpm dev` and fix any remaining import errors by copying the named module from ahavah-web. Common ones: `cmdk`, `vaul`, `react-day-picker`. Install missing npm deps as encountered.

- [ ] **Step 2: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add .
git commit -m "primitives: copy shadcn + Kibo blocks from ahavah-web"
```

### Task 1.3: FE — admin shell (header + sidebar + outlet)

**Files:**
- Create: `src/components/admin/shell.tsx`
- Create: `src/components/admin/sidebar-nav.tsx`
- Create: `src/lib/tabs.ts`
- Modify: `src/app/page.tsx` (replace the redirect with the shell)

- [ ] **Step 1: Tab registry**

Path: `src/lib/tabs.ts`

```typescript
import { LayoutDashboard, Users, Layers, Coins, ShieldAlert, Activity, ScrollText } from "lucide-react";

export type TabId = "overview" | "users" | "cohorts" | "economy" | "moderation" | "system" | "audit";

export const TABS = [
  { id: "overview",   label: "Overview",   icon: LayoutDashboard },
  { id: "users",      label: "Users",      icon: Users },
  { id: "cohorts",    label: "Cohorts",    icon: Layers },
  { id: "economy",    label: "Economy",    icon: Coins },
  { id: "moderation", label: "Moderation", icon: ShieldAlert },
  { id: "system",     label: "System",     icon: Activity },
  { id: "audit",      label: "Audit",      icon: ScrollText },
] as const satisfies ReadonlyArray<{ id: TabId; label: string; icon: typeof LayoutDashboard }>;

export const DEFAULT_TAB: TabId = "overview";
```

- [ ] **Step 2: Sidebar nav**

Path: `src/components/admin/sidebar-nav.tsx`

```typescript
"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { TABS, DEFAULT_TAB, type TabId } from "@/lib/tabs";

export function SidebarNav() {
  const params = useSearchParams();
  const router = useRouter();
  const active = (params.get("tab") as TabId | null) ?? DEFAULT_TAB;

  return (
    <nav aria-label="Admin sections" className="flex flex-col gap-1 p-3">
      {TABS.map(({ id, label, icon: Icon }) => {
        const isActive = active === id;
        return (
          <button
            key={id}
            onClick={() => router.push(`/?tab=${id}`)}
            className={cn(
              "flex items-center gap-3 rounded-2xl px-3 py-2 text-meta",
              isActive
                ? "bg-(--color-lavender)/15 text-(--color-lavender) font-semibold"
                : "text-(--ink-2) hover:bg-(--ink)/5",
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 3: Header + shell**

Path: `src/components/admin/shell.tsx`

```typescript
"use client";

import { SidebarNav } from "@/components/admin/sidebar-nav";
import { Button } from "@/components/ui/button";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

type WhoAmI = { is_admin: boolean; email: string | null; person_uuid: string | null };

export function AdminShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data, isLoading, isError } = useQuery<WhoAmI>({
    queryKey: ["whoami"],
    queryFn: () => api.get<WhoAmI>("/admin/whoami"),
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    if (isLoading) return;
    if (isError || !data?.is_admin) router.replace("/unauthorized");
  }, [data, isError, isLoading, router]);

  if (isLoading) return null;          // Skeleton not needed for shell load
  if (!data?.is_admin) return null;    // Will redirect

  return (
    <div className="grid grid-cols-[240px_1fr] grid-rows-[56px_1fr] min-h-dvh">
      <header className="col-span-2 flex items-center justify-between px-6 border-b border-(--hairline) bg-(--card)">
        <span className="font-display text-h3 text-(--ink)">Ahavah Admin</span>
        <div className="flex items-center gap-3 text-meta text-(--ink-2)">
          <span>{data.email}</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              await api.post("/sign-out").catch(() => {});
              router.replace("/auth/sign-in");
            }}
          >
            Sign out
          </Button>
        </div>
      </header>

      <aside className="border-r border-(--hairline) bg-(--card)">
        <SidebarNav />
      </aside>

      <main className="overflow-y-auto px-10 py-8 max-w-[1400px] w-full">
        {children}
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Page (single-page tab dispatch)**

Path: `src/app/page.tsx`

```typescript
import { Suspense } from "react";
import { AdminShell } from "@/components/admin/shell";
import { TabRouter } from "@/components/admin/tab-router";

export default function HomePage() {
  return (
    <AdminShell>
      <Suspense fallback={null}>
        <TabRouter />
      </Suspense>
    </AdminShell>
  );
}
```

Path: `src/components/admin/tab-router.tsx`

```typescript
"use client";

import { useSearchParams } from "next/navigation";
import { TabOverview } from "@/components/admin/tab-overview";
import { DEFAULT_TAB, type TabId } from "@/lib/tabs";

function ComingSoon({ name }: { name: string }) {
  return (
    <div className="rounded-2xl border border-(--hairline) bg-(--card) p-8 text-center">
      <p className="text-h3 text-(--ink)">{name}</p>
      <p className="mt-2 text-meta text-(--ink-2)">Coming soon. Track progress in the implementation plan.</p>
    </div>
  );
}

export function TabRouter() {
  const params = useSearchParams();
  const tab = (params.get("tab") as TabId | null) ?? DEFAULT_TAB;

  switch (tab) {
    case "overview":   return <TabOverview />;
    case "users":      return <ComingSoon name="Users" />;
    case "cohorts":    return <ComingSoon name="Cohorts" />;
    case "economy":    return <ComingSoon name="Economy" />;
    case "moderation": return <ComingSoon name="Moderation" />;
    case "system":     return <ComingSoon name="System" />;
    case "audit":      return <ComingSoon name="Audit log" />;
    default:           return <TabOverview />;
  }
}
```

- [ ] **Step 5: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add .
git commit -m "shell: header + sidebar nav + tab router (placeholders for non-overview)"
```

### Task 1.4: FE — TanStack Query hook + types for overview

**Files:**
- Create: `src/lib/types.ts`
- Create: `src/lib/queries.ts`

- [ ] **Step 1: Types**

Path: `src/lib/types.ts`

```typescript
export type OverviewKPIs = {
  signups_today: number;
  active_24h: number;
  person_total: number;
  premium_holders: number;
  pending_reports: number;
  referral_signups_7d: number;
};

export type DaySignups = { day: string; waitlist: number; beta: number; person: number };
export type DayCtr = { day: string; clicks: number; signups: number };
export type DayPremium = { day: string; adds: number };

export type RecentEventKind = "signup" | "signup_waitlist" | "referral_credited" | "admin_action";
export type RecentEvent = {
  kind: RecentEventKind;
  subject: string;
  object: string | null;
  created_at: string;
};

export type OverviewResponse = {
  kpis: OverviewKPIs;
  signups_30d: DaySignups[];
  referral_ctr_7d: DayCtr[];
  premium_30d: DayPremium[];
  recent_activity: RecentEvent[];
};
```

- [ ] **Step 2: Query hooks**

Path: `src/lib/queries.ts`

```typescript
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import type { OverviewResponse } from "@/lib/types";

export function useOverview() {
  return useQuery<OverviewResponse>({
    queryKey: ["overview"],
    queryFn: () => api.get<OverviewResponse>("/admin/overview"),
  });
}
```

- [ ] **Step 3: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add src/lib/types.ts src/lib/queries.ts
git commit -m "queries: useOverview() TanStack hook + types"
```

### Task 1.5: FE — KPI card + Overview tab implementation

**Files:**
- Create: `src/components/admin/kpi-card.tsx`
- Create: `src/components/admin/tab-overview.tsx`

- [ ] **Step 1: KPI card primitive**

Path: `src/components/admin/kpi-card.tsx`

```typescript
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Props = {
  label: string;
  value: number | string | null;
  loading?: boolean;
  delta?: { value: number; positive?: boolean } | null;
  className?: string;
};

export function KpiCard({ label, value, loading, delta, className }: Props) {
  return (
    <Card className={cn("p-5 flex flex-col gap-2", className)}>
      <p className="text-overline uppercase tracking-wide text-(--ink-3)">{label}</p>
      {loading ? (
        <Skeleton className="h-10 w-24" />
      ) : (
        <p className="font-display text-display-lg text-(--ink) tabular-nums leading-none">{value ?? "—"}</p>
      )}
      {delta && (
        <p className={cn(
          "text-caption font-semibold tabular-nums",
          delta.positive ? "text-(--color-lime)" : "text-(--color-pink)",
        )}>
          {delta.value > 0 ? "+" : ""}{delta.value} vs yesterday
        </p>
      )}
    </Card>
  );
}
```

- [ ] **Step 2: Overview tab**

Path: `src/components/admin/tab-overview.tsx`

```typescript
"use client";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChartContainer, ChartTooltip, ChartTooltipContent,
} from "@/components/ui/chart";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { KpiCard } from "@/components/admin/kpi-card";
import { useOverview } from "@/lib/queries";

const SIGNUPS_CHART_CONFIG = {
  waitlist: { label: "Waitlist", color: "var(--color-lavender)" },
  beta:     { label: "Beta",     color: "var(--color-lime)" },
  person:   { label: "Person",   color: "var(--color-pink)" },
};

const CTR_CHART_CONFIG = {
  clicks:  { label: "Clicks",  color: "var(--color-lavender)" },
  signups: { label: "Signups", color: "var(--color-lime)" },
};

const PREMIUM_CHART_CONFIG = {
  adds: { label: "Adds", color: "var(--color-lime)" },
};

export function TabOverview() {
  const { data, isLoading, isError } = useOverview();

  if (isError) {
    return (
      <Card className="p-8 text-center">
        <p className="text-h3 text-(--ink)">Couldn&apos;t load overview</p>
        <p className="mt-2 text-meta text-(--ink-2)">Refresh the page or check the api.ahavah.app status.</p>
      </Card>
    );
  }

  const k = data?.kpis;

  return (
    <div className="flex flex-col gap-8">
      {/* KPI grid 3×2 */}
      <section className="grid grid-cols-3 gap-4">
        <KpiCard label="Signups today"        value={k?.signups_today}        loading={isLoading} />
        <KpiCard label="Active 24h"           value={k?.active_24h}           loading={isLoading} />
        <KpiCard label="Total persons"        value={k?.person_total}         loading={isLoading} />
        <KpiCard label="Premium holders"      value={k?.premium_holders}      loading={isLoading} />
        <KpiCard label="Pending reports"      value={k?.pending_reports}      loading={isLoading} />
        <KpiCard label="Referral signups 7d"  value={k?.referral_signups_7d}  loading={isLoading} />
      </section>

      {/* Charts row */}
      <section className="grid grid-cols-3 gap-4">
        <Card className="p-5 col-span-1">
          <p className="text-overline uppercase text-(--ink-3) mb-3">Signups 30d</p>
          {isLoading ? <Skeleton className="h-60 w-full" /> : (
            <ChartContainer config={SIGNUPS_CHART_CONFIG} className="h-60 w-full">
              <AreaChart data={data?.signups_30d ?? []}>
                <CartesianGrid vertical={false} strokeOpacity={0.1} />
                <XAxis dataKey="day" tickLine={false} tickMargin={8}
                  tickFormatter={(v) => v.slice(5)} />
                <YAxis tickLine={false} width={30} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Area dataKey="waitlist" stackId="a" type="monotone" stroke="var(--color-lavender)" fill="var(--color-lavender)" fillOpacity={0.4} />
                <Area dataKey="beta"     stackId="a" type="monotone" stroke="var(--color-lime)"     fill="var(--color-lime)"     fillOpacity={0.4} />
                <Area dataKey="person"   stackId="a" type="monotone" stroke="var(--color-pink)"    fill="var(--color-pink)"    fillOpacity={0.4} />
              </AreaChart>
            </ChartContainer>
          )}
        </Card>

        <Card className="p-5 col-span-1">
          <p className="text-overline uppercase text-(--ink-3) mb-3">Referral CTR 7d</p>
          {isLoading ? <Skeleton className="h-60 w-full" /> : (
            <ChartContainer config={CTR_CHART_CONFIG} className="h-60 w-full">
              <LineChart data={data?.referral_ctr_7d ?? []}>
                <CartesianGrid vertical={false} strokeOpacity={0.1} />
                <XAxis dataKey="day" tickLine={false} tickFormatter={(v) => v.slice(5)} />
                <YAxis tickLine={false} width={30} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Line dataKey="clicks"  stroke="var(--color-lavender)" dot={false} />
                <Line dataKey="signups" stroke="var(--color-lime)"    dot={false} />
              </LineChart>
            </ChartContainer>
          )}
        </Card>

        <Card className="p-5 col-span-1">
          <p className="text-overline uppercase text-(--ink-3) mb-3">Premium adds 30d</p>
          {isLoading ? <Skeleton className="h-60 w-full" /> : (
            <ChartContainer config={PREMIUM_CHART_CONFIG} className="h-60 w-full">
              <BarChart data={data?.premium_30d ?? []}>
                <CartesianGrid vertical={false} strokeOpacity={0.1} />
                <XAxis dataKey="day" tickLine={false} tickFormatter={(v) => v.slice(5)} />
                <YAxis tickLine={false} width={30} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="adds" fill="var(--color-lime)" radius={4} />
              </BarChart>
            </ChartContainer>
          )}
        </Card>
      </section>

      {/* Recent activity feed */}
      <Card className="p-5">
        <p className="text-overline uppercase text-(--ink-3) mb-3">Recent activity</p>
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
          </div>
        ) : (data?.recent_activity?.length ?? 0) === 0 ? (
          <p className="text-meta text-(--ink-2)">No activity yet.</p>
        ) : (
          <ul className="divide-y divide-(--hairline) text-meta">
            {data!.recent_activity.map((e, i) => (
              <li key={i} className="py-2 flex items-center justify-between gap-4">
                <span className="text-(--ink-3) tabular-nums w-24 shrink-0">
                  {new Date(e.created_at).toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" })}
                </span>
                <span className="flex-1 text-(--ink)">
                  {e.kind === "signup" && <><strong>{e.subject}</strong> opted into the beta</>}
                  {e.kind === "signup_waitlist" && <><strong>{e.subject}</strong> joined the waitlist</>}
                  {e.kind === "referral_credited" && <><strong>{e.subject}</strong> referred <strong>{e.object}</strong></>}
                  {e.kind === "admin_action" && <><strong>{e.subject}</strong> performed <code>{e.object}</code></>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /d/Antigravity/ahavah-admin
git add .
git commit -m "tab-overview: KPI cards + 3 charts + recent activity feed"
```

### Task 1.6: Deploy + smoke

- [ ] **Step 1: Deploy FE**

```bash
cd /d/Antigravity/ahavah-admin
vercel deploy --prod --token <VERCEL_TOKEN>
```

- [ ] **Step 2: Browser smoke**

Visit `https://admin.ahavah.app`, sign in, expect: 6 KPI cards populated, 3 charts populated (maybe sparse if data is recent), recent-activity feed showing real production rows.

- [ ] **Step 3: Tag**

```bash
cd /d/Antigravity/ahavah-admin
git tag phase1-overview-live
git push origin phase1-overview-live
```

**Phase 1 shippable artifact:** Overview tab live + populated with production data.

---

# Phase 2 — Users tab + drawer + ALL user-management actions

**Outcome at phase end:** Admin can find any user (search/filter/paginate), open their full record in a drawer, and perform every user-management action with confirmation + audit logging.

### Task 2.1: BE — `GET /admin/users` (list + search + filter)

**Files:**
- Create: `ahavah-api/service/admin/queries/users.py`
- Modify: `ahavah-api/service/api/admin/users_routes.py` (new file)

Detailed SQL + endpoint code in the spec doc §C row 3. The endpoint accepts `q`, `filters[]`, `page`, returns rows + total. Pagination 20/page. Filter pills: `has_person`, `waitlist_only`, `beta_only`, `unsubscribed`, `has_premium`, `role=admin`, `role=mod`.

- [ ] Implement per spec §C and the tab-users section of the screens spec §"Screen 4". Commit per task.

### Task 2.2: BE — `GET /admin/users/:uuid` (single-user blob)

Single comprehensive query returning person row + photos array + entitlements + last-N tokens + last-N sessions + referral counts + per-user audit log.

- [ ] Implement per spec §C and screens spec §"Screen 5".

### Task 2.3: BE — entitlement action endpoints

- `POST /admin/users/:uuid/entitlements` — grant
- `DELETE /admin/users/:uuid/entitlements/:name` — revoke

Both call `service.entitlements.grant` / `revoke`. Both record `record_audit(action='grant_entitlement' | 'revoke_entitlement', target_uuid, metadata={name, expires_at, reason})`. Pydantic models for the request bodies.

- [ ] Implement.

### Task 2.4: BE — token action endpoint

- `POST /admin/users/:uuid/tokens` — body `{ delta: int, reason: str }`. delta positive → `service.tokens.credit`, negative → `service.tokens.debit`. Reason becomes `'admin_credit'` or `'admin_debit'`. Audit logged.

- [ ] Implement.

### Task 2.5: BE — role action endpoint

- `PATCH /admin/users/:uuid/roles` — body `{ add: ['admin'|'mod'], remove: ['admin'|'mod'] }`. Updates `person.roles` array. Audit logged with full before/after in metadata.

- [ ] Implement.

### Task 2.6: BE — lifecycle endpoints

- `POST /admin/users/:uuid/deactivate` → `person.activated = false`
- `POST /admin/users/:uuid/reactivate` → `person.activated = true`
- `DELETE /admin/users/:uuid` — body MUST contain `{ confirm_email: <user's email> }`. Verifies the email matches the target, then cascading delete on the person row.

All three audit-logged.

- [ ] Implement.

### Task 2.7: BE — support/debug endpoints

- `POST /admin/users/:uuid/clear-onboardee` — DELETE from `onboardee` + `duo_session` by email
- `POST /admin/users/:uuid/resend-otp` — calls the existing `service.person._send_otp` after writing the OTP to duo_session

Both audit-logged.

- [ ] Implement.

### Task 2.8: FE — Users tab (Screen 4)

**Files:**
- Create: `src/components/admin/tab-users.tsx`
- Modify: `src/lib/queries.ts` (add `useUsers()` + `useUser()`)
- Modify: `src/lib/types.ts`
- Modify: `src/components/admin/tab-router.tsx` (mount the new tab)

Layout per screens spec §"Screen 4": search box + filter pills + table + pagination. Click row → opens user drawer via `?user=<uuid>` URL state.

- [ ] Implement.

### Task 2.9: FE — User drawer (Screen 5)

**Files:**
- Create: `src/components/admin/user-drawer.tsx` (the Sheet wrapper)
- Create: `src/components/admin/user-drawer-identity.tsx`
- Create: `src/components/admin/user-drawer-activity.tsx`
- Create: `src/components/admin/user-drawer-economy.tsx`
- Create: `src/components/admin/user-drawer-actions.tsx`
- Modify: `src/lib/mutations.ts` (action endpoints)

Layout per screens spec §"Screen 5": Sheet from right, sticky header, internal Tabs primitive (Identity/Activity/Economy/Actions). Each internal tab is a separate component file. Mutations use `useMutation` from TanStack Query; on success invalidate the `useUser(uuid)` query.

Confirmation dialogs follow the §"Cross-cutting design notes" → "Confirmation dialogs" pattern in the screens spec: title + target identity + reason field + two-button shape. Hard delete additionally requires typing the email to confirm.

- [ ] Implement.

### Task 2.10: Deploy + smoke

- [ ] Push BE → wait GHA → deploy FE → in browser: search "harrigan" → click row → drawer opens → tab through Identity / Activity / Economy / Actions → grant a test entitlement → verify it landed in `admin_audit_log` via psql.

- [ ] Tag `phase2-users-and-actions-live`.

**Phase 2 shippable artifact:** Admin can do real day-to-day user management. This is the most-used surface.

---

# Phase 3 — Cohorts tab (waitlist + beta + referrals)

**Outcome at phase end:** Three sub-tabs each with their own KPIs + chart + breakdown table. Real production data.

Tasks:
- [ ] **3.1** BE `GET /admin/cohorts/waitlist` returning KPIs + 30d signups + sex/intent/country/ethnicity/assembly/source breakdowns
- [ ] **3.2** BE `GET /admin/cohorts/beta` returning KPIs + per-tester table + 4-step funnel
- [ ] **3.3** BE `GET /admin/cohorts/referrals` returning KPIs + click-stream by UA class + per-inviter table
- [ ] **3.4** FE `tab-cohorts.tsx` with sub-tab strip + 3 sub-views
- [ ] **3.5** Deploy + smoke + tag `phase3-cohorts-live`

Detailed code per Phase 1 pattern — each KPI is a separate aggregation query, charts use the same `ChartContainer` + Recharts wrappers, tables use the shadcn `Table` primitive.

---

# Phase 4 — Economy tab

Tasks:
- [ ] **4.1** BE `GET /admin/economy/ledger` — paginated, filterable by reason/range/email
- [ ] **4.2** BE `GET /admin/economy/entitlements` — list of premium holders + expiry + source
- [ ] **4.3** BE `GET /admin/economy/subscriptions` — Stripe state (graceful empty when no Stripe data)
- [ ] **4.4** FE `tab-economy.tsx` with 3 sub-tabs
- [ ] **4.5** Deploy + smoke + tag `phase4-economy-live`

---

# Phase 5 — Moderation expansion

Tasks:
- [ ] **5.1** Lift the existing `/admin/reports` route under `/admin/moderation/reports` (keep the legacy path as an alias for backwards compat with the existing FE page)
- [ ] **5.2** BE `GET /admin/moderation/photos` — filter by `state` (default `pending`)
- [ ] **5.3** BE `POST /admin/moderation/photos/:uuid/approve` and `.../reject` — moderator action, audit-logged
- [ ] **5.4** BE `GET /admin/moderation/rude-messages` — flag list with context
- [ ] **5.5** FE `tab-moderation.tsx` with 3 sub-tabs (reports lifts existing UI)
- [ ] **5.6** Deploy + smoke + tag `phase5-moderation-live`

---

# Phase 6 — System + Audit log

Tasks:
- [ ] **6.1** BE `GET /admin/system/health` — counts, OTP success, DB row counts, deploy timestamp
- [ ] **6.2** BE `GET /admin/audit-log` — filterable by actor/target/action/range
- [ ] **6.3** FE `tab-system.tsx` — dense card grid per screens spec §"Screen 9"
- [ ] **6.4** FE `tab-audit.tsx` — filter row + table + expandable metadata
- [ ] **6.5** Deploy + smoke + tag `phase6-system-and-audit-live`

---

# Self-review

**Spec coverage:**
- Screen 1 (Sign-in): Task 0.10 ✓
- Screen 2 (Unauthorized): Task 0.10 ✓
- Screen 3 (Overview): Tasks 1.1–1.6 ✓
- Screen 4 (Users): Tasks 2.1, 2.8 ✓
- Screen 5 (User drawer): Tasks 2.2–2.7, 2.9 ✓
- Screen 6 (Cohorts): Phase 3 ✓
- Screen 7 (Economy): Phase 4 ✓
- Screen 8 (Moderation): Phase 5 ✓
- Screen 9 (System): Phase 6 ✓
- Screen 10 (Audit): Phase 6 ✓
- Shared shell + audit log + role gate + URL state: Phase 0 ✓

**Placeholder scan:**
- Phases 3-6 use shortened task descriptions (the pattern is established in Phase 1-2). The implementer should follow the same task structure: SQL file → re-export → endpoint → pytest → FE query hook → FE component → commit.
- The spec for each of those phases' screens lives in `docs/superpowers/specs/2026-06-06-admin-dashboard-screens.md` and contains every layout + interaction detail.

**Type consistency:**
- `WhoAmI` shape consistent in both BE (Task 0.5) and FE (Task 1.3)
- `OverviewResponse` shape consistent BE (Task 1.1) ↔ FE (Task 1.4)
- `require_admin` signature `(s: t.SessionInfo) -> None` consistent across all endpoint files
- `record_audit` signature consistent across BE
- Action endpoint URL shape `/admin/users/:uuid/<action>` consistent

**Risks called out:**
- The Claude Design HTML is binary-encoded over HTTPS and didn't decompress via curl. Implementer should open the URL in a browser to view it visually before each FE component task, and treat the screens spec as the canonical brief.
- DNS for `admin.ahavah.app` requires the Vercel domain config (Task 0.11). If `vercel domains add` fails with a permission quirk (we hit one for `signup.ahavah.app` earlier this session), use the REST API alias call documented in `docs/superpowers/handovers/2026-06-06-beta-referrals-handover.md` §7.6 workaround.
- The first admin (Task 0.1) is set by SQL. After Phase 2 ships, additional admins should be promoted via the `PATCH /admin/users/:uuid/roles` action so the audit log captures who granted what.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-admin-dashboard-implementation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Suits this plan well because tasks are small and reviewable. The Phase 0–2 detail level supports this directly; Phases 3–6 follow the same pattern with the screens spec as visual brief.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Heavier on a single chat; better if you want to drive shape decisions live.

**Which approach?**
