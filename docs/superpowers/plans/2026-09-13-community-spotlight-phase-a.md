Linear: TEC-862

# Community Spotlight, Phase A (data, consent, emails, Growth stats) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the member-facing Spotlight consent, the three campaign emails (announcement, weekly community, re-invite) with a send log and frequency cap, campaign click links, and the Growth tab's stats and email panels on admin.ahavah.app, all sendable without shell access.

**Architecture:** Postgres columns and two tables in `duo_api` (migration 0039) carry consent, send history and click attribution. A `service/growth` package holds every cohort query so stats and emails read one source. Emails stay on the canonical shell (`emails/base.py`) and are triggered by admin-gated API endpoints that the Growth tab calls; CLI scripts remain for operator fallback. Signed action links never change state on GET: they land on a confirmation page whose button submits a POST. Phase B (queue, worker, cards, member approval, member of the week) is a separate plan that builds on these tables.

**Tech Stack:** Flask + psycopg + pydantic (`ahavah-api`), pytest in the disposable Docker stack; Next.js 16 + React 19 + Tailwind v4 (`ahavah-web`, `ahavah-admin`), vitest, playwright-core; SES SMTP; Claude Design via `/brief` and `/handoff`.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md`

## Global Constraints

- Committing to `ahavah-api` or pushing `ahavah-web` and `ahavah-admin` deploys to production. Hold every commit until the owner says go for that package; run the full gate before asking.
- No em dashes in any user-facing string. Sentence case. No literal `%` inside SQL strings passed to psycopg (use `%%` or avoid).
- No link may change state on GET. Action links land on a page with a POST button.
- Names on social only with opt-in and per-card approval (Phase B). Names in members-only email are allowed.
- Emails and captions are English only in this version.
- Backend tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` from `d:/Antigravity/ahavah-api`. If `FATAL: database "duo_api" does not exist`, reapply `migrations/*.sql` with psql. Baseline today: 235 passed.
- Web tests: `pnpm test` (baseline 525), `npx tsc --noEmit`, `npx eslint <files> --max-warnings=0`. Admin: `npx tsc --noEmit`, `node --test tests/`.
- Every new or altered surface comes from a Claude Design brief (Task 2) and is transcribed faithfully; never hand-roll UI.
- Migration numbering: next free is `0039`. Applied files are immutable; corrections are new migrations.
- Prod DB reads for verification are allowed; prod data mutation only with the owner's explicit go.

---

### Task 1: Pre-flight checks and the unsatisfiable nudge removal

**Files:**
- Modify: `d:/Antigravity/ahavah-web/src/lib/use-next-action.ts` (the `profile-nudge` block, around line 502)
- Modify: `d:/Antigravity/ahavah-web/src/lib/next-action.ts` (`NEXT_ACTION_RANK` and the `NextActionKind` union)
- Test: `d:/Antigravity/ahavah-web/tests/lib/next-action.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: a written pre-flight record at `d:/Antigravity/ahavah-api/docs/superpowers/plans/2026-09-13-spotlight-preflight.md`; the `profile-nudge` kind removed from the next-action ranking.

- [ ] **Step 1: Record the pre-flight facts (read-only)**

Run each and paste the output into the pre-flight record:

```bash
# Pending scheduled email waves on the droplet (spec 8.4)
ssh -i ~/.ssh/id_ed25519_ahavah -6 root@2604:a880:800:14:0:2:f28f:5000 'atq; crontab -l 2>/dev/null | grep -v "^#"; ls /etc/cron.d'
# Owner account admin role (spec 8.3): expect 0 rows for harrigan.tennyson@gmail.com today
ssh -i ~/.ssh/id_ed25519_ahavah -6 root@2604:a880:800:14:0:2:f28f:5000 'docker exec $(docker ps -qf name=postgres | head -1) psql -U postgres -d duo_api -c "SELECT p.email, r.name FROM person p JOIN person_role pr ON pr.person_id=p.id JOIN role r ON r.id=pr.role_id WHERE r.name='"'"'admin'"'"';"'
# Vercel plan for the admin project (spec 8.2)
cd /d/Antigravity/ahavah-admin && npx vercel project ls 2>/dev/null | head -5; npx vercel teams ls 2>/dev/null | head -5
```

Expected: a list of pending jobs (possibly empty), the admin-role rows, and the team name. Write the three findings plus the Meta app question (spec 8.1, owner action) into the pre-flight record with today's date. Do not grant the role or delete jobs; both are owner decisions listed at the top of the record.

- [ ] **Step 2: Write the failing test for nudge removal**

Append to `tests/lib/next-action.test.ts`:

```ts
describe("profile-nudge removal", () => {
  it("never ranks a profile-nudge kind because no screen can satisfy it", () => {
    expect(Object.keys(NEXT_ACTION_RANK)).not.toContain("profile-nudge");
  });
  it("selects premium-upsell, not an answers nudge, for a complete member without prompt cards", () => {
    const live = [
      { kind: "premium-upsell" as const, primary: { kind: "premium-upsell" } as never, row: { kind: "premium-upsell" } as never },
    ];
    const { primary } = selectNextAction(live);
    expect(primary?.kind).toBe("premium-upsell");
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /d/Antigravity/ahavah-web && pnpm test -- tests/lib/next-action.test.ts`
Expected: FAIL, `NEXT_ACTION_RANK` contains `"profile-nudge"`.

- [ ] **Step 4: Remove the kind**

In `src/lib/next-action.ts` delete `"profile-nudge"` from the `NextActionKind` union and from `NEXT_ACTION_RANK`. In `src/lib/use-next-action.ts` delete the whole `// 6. profile-nudge` block (the `hasAnswers` constant and the `live.push({ kind: "profile-nudge", ... })`). Update the rank-table snapshot test if one asserts the full order. Leave a one-line comment where the block was:

```ts
// profile-nudge removed 2026-09-13: no screen writes promptCards, so the
// "Add two more answers" nudge was unsatisfiable for every member.
```

- [ ] **Step 5: Run the suite and gates**

Run: `pnpm test && npx tsc --noEmit && npx eslint src/lib/next-action.ts src/lib/use-next-action.ts tests/lib/next-action.test.ts --max-warnings=0`
Expected: all green; test count drops only by tests that asserted the removed kind.

- [ ] **Step 6: Commit (hold push until owner go)**

```bash
git add src/lib/next-action.ts src/lib/use-next-action.ts tests/lib/next-action.test.ts
git commit -m "fix(discover): remove the unsatisfiable answers nudge from next-action"
```

---

### Task 2: Design round-trip (brief, then handoff)

**Files:**
- Create: `d:/Antigravity/ahavah-web/docs/design-briefs/2026-09-13-community-spotlight.md`
- Create (after handoff): `d:/Antigravity/ahavah-web/public/email/title-spotlight.png`, `title-spotlight-wht.png`, `title-community.png` already exists (reuse for E2), `title-reinvite.png`, `title-reinvite-wht.png`
- Create (after handoff): `d:/Antigravity/ahavah-admin/Claude Design/Growth Tab.html`, `d:/Antigravity/ahavah-web/Claude Design/Spotlight Confirm.html`

**Interfaces:**
- Produces: SOT HTML for the Growth tab (stats panel, emails panel), the Spotlight confirmation page, the opt-in switch row in privacy settings, and Ultra title image pairs named exactly as above. Phase B's card template is requested in the same brief so one round-trip covers both phases.

- [ ] **Step 1: Write the brief**

Invoke `/brief` with this content saved at the path above. Required sections: context (Ahavah, tokens, admin kit at `ahavah-admin/design-source/`), the surfaces (Growth tab desktop 1440 with a mobile read-only 390 frame; Spotlight confirm page at 390 and 1440 with a single POST button and an opted-in success state; the privacy settings switch row "Feature me in Spotlight" with helper text listing exactly what is shared; three Ultra title images "Meet Spotlight.", "New faces since you were away.", reuse "title-community"; the Phase B card template square 1080 with photo, roundup collage and member of the week variants, fonts Ultra and Plus Jakarta Sans with Hebrew and Latin diacritic coverage), copy rules (no em dashes, sentence case), evidence screenshots of the current admin Overview tab and privacy page at 390 taken with the invite harness pattern, and what is out of scope (stories, reels, any Discover change).

- [ ] **Step 2: Push it**

Run `/brief` and confirm the DesignSync result lists the brief and assets under `briefs/` in project `bd2e1ca9-f678-4567-a6eb-79afd75b2ef7`. Hand the paste line to the owner.

- [ ] **Step 3: Retrieve when executed**

Run `/handoff community spotlight`. Write text files into the drop folders above; list binaries (the PNG title images) for one-time manual download and place them under `ahavah-web/public/email/` with the exact names. Verify each PNG opens and has a transparent background at the width the email will use.

- [ ] **Step 4: Commit the brief only**

```bash
cd /d/Antigravity/ahavah-web && git add docs/design-briefs/2026-09-13-community-spotlight.md && git commit -m "docs(design): community spotlight brief"
```

---

### Task 3: Migration 0039 (consent columns, send log, campaign links)

**Files:**
- Create: `migrations/0039_community_spotlight.sql`
- Test: `tests/test_migration_0039.py`

**Interfaces:**
- Produces: columns `person.spotlight_opt_in`, `person.spotlight_opt_in_at`, `person.spotlight_last_featured_at`, `person.reinvite_sent_at`; table `email_send_log(person_id, campaign, campaign_id, message_id, sent_at)`; table `campaign_link(key, kind, target_url, subject_person_id, created_at)`; table `campaign_click(link_key, clicked_at, ua_class)`; column `person.spotlight_ref` (text, nullable).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_0039.py
from database import api_tx

def _cols(tx, table):
    return {r['column_name'] for r in tx.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %(t)s",
        dict(t=table)).fetchall()}

def test_0039_person_columns_and_tables_exist():
    with api_tx('read committed') as tx:
        pc = _cols(tx, 'person')
        assert {'spotlight_opt_in', 'spotlight_opt_in_at',
                'spotlight_last_featured_at', 'reinvite_sent_at',
                'spotlight_ref'} <= pc
        assert {'person_id', 'campaign', 'campaign_id', 'message_id', 'sent_at'} <= _cols(tx, 'email_send_log')
        assert {'key', 'kind', 'target_url', 'subject_person_id', 'created_at'} <= _cols(tx, 'campaign_link')
        assert {'link_key', 'clicked_at', 'ua_class'} <= _cols(tx, 'campaign_click')

def test_0039_send_log_unique_per_campaign_run(make_person):
    import psycopg, pytest
    p = make_person(name='Log')
    with api_tx() as tx:
        tx.execute("INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id) VALUES (%(id)s, 'e1', 'run-1', 'm1')", dict(id=p['id']))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with api_tx() as tx:
            tx.execute("INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id) VALUES (%(id)s, 'e1', 'run-1', 'm2')", dict(id=p['id']))
```

- [ ] **Step 2: Run to verify it fails**

Run the backend test command scoped to `tests/test_migration_0039.py`.
Expected: FAIL, columns missing.

- [ ] **Step 3: Write the migration**

```sql
-- migrations/0039_community_spotlight.sql
-- Community Spotlight, Phase A. Idempotent.
ALTER TABLE person
  ADD COLUMN IF NOT EXISTS spotlight_opt_in boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS spotlight_opt_in_at timestamptz,
  ADD COLUMN IF NOT EXISTS spotlight_last_featured_at timestamptz,
  ADD COLUMN IF NOT EXISTS reinvite_sent_at timestamptz,
  ADD COLUMN IF NOT EXISTS spotlight_ref text;

CREATE TABLE IF NOT EXISTS email_send_log (
  id          bigserial PRIMARY KEY,
  person_id   int NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  campaign    text NOT NULL,
  campaign_id text NOT NULL,
  message_id  text,
  sent_at     timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE (person_id, campaign, campaign_id)
);
CREATE INDEX IF NOT EXISTS email_send_log_person_sent_idx ON email_send_log (person_id, sent_at DESC);

CREATE TABLE IF NOT EXISTS campaign_link (
  key               text PRIMARY KEY,
  kind              text NOT NULL,
  target_url        text NOT NULL,
  subject_person_id int REFERENCES person(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaign_click (
  id         bigserial PRIMARY KEY,
  link_key   text NOT NULL REFERENCES campaign_link(key) ON DELETE CASCADE,
  clicked_at timestamptz NOT NULL DEFAULT NOW(),
  ua_class   text NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS campaign_click_key_idx ON campaign_click (link_key, clicked_at DESC);
```

- [ ] **Step 4: Apply in the test stack and run**

The test runner applies `migrations/*.sql` on a fresh stack; if the stack is warm, apply manually: `docker compose -f docker-compose.test.yml exec postgres psql -U postgres -d duo_api -v ON_ERROR_STOP=1 -f /app/migrations/0039_community_spotlight.sql` (mount is `/app`). Run the test.
Expected: PASS for both tests.

- [ ] **Step 5: Commit (hold)**

```bash
git add migrations/0039_community_spotlight.sql tests/test_migration_0039.py
git commit -m "feat(db): community spotlight consent, send log, campaign links (0039)"
```

---

### Task 4: `service/campaigns` send log, frequency cap, campaign links

**Files:**
- Create: `service/campaigns/__init__.py`
- Create: `service/api/campaign_link_routes.py`
- Modify: `service/api/__init__.py` (add `import service.api.campaign_link_routes  # noqa: E402,F401` next to the unsubscribe import at line 1190)
- Test: `tests/test_campaigns.py`

**Interfaces:**
- Produces:
  - `can_send(tx, person_id: int, campaign: str, campaign_id: str, *, cap_days: int = 7, exempt: bool = False) -> bool` (False when the same run already sent to this person, or any campaign email was sent inside `cap_days` and not exempt)
  - `log_send(tx, person_id: int, campaign: str, campaign_id: str, message_id: str | None) -> None`
  - `make_campaign_link(tx, kind: str, target_url: str, subject_person_id: int | None = None) -> str` returning the absolute URL `{WEB_BASE_URL}/s/<key>`
  - `record_click(tx, key: str, user_agent: str) -> str | None` returning the target URL or None when unknown
  - HTTP `GET /s/<key>` on the API: 302 to the target and one `campaign_click` row; unknown key 404.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_campaigns.py
from database import api_tx
from service.campaigns import can_send, log_send, make_campaign_link, record_click

def test_cap_blocks_second_campaign_within_seven_days(make_person):
    p = make_person(name='Cap')
    with api_tx() as tx:
        assert can_send(tx, p['id'], 'e1', 'run-a')
        log_send(tx, p['id'], 'e1', 'run-a', 'mid-1')
        assert not can_send(tx, p['id'], 'e1', 'run-a')          # same run, idempotent
        assert not can_send(tx, p['id'], 'e3', 'run-b')          # cap
        assert can_send(tx, p['id'], 'e4', 'run-c', exempt=True) # member-triggered

def test_cap_expires_after_window(make_person):
    p = make_person(name='Old')
    with api_tx() as tx:
        log_send(tx, p['id'], 'e1', 'run-old', 'mid')
        tx.execute("UPDATE email_send_log SET sent_at = NOW() - interval '8 days' WHERE person_id = %(id)s", dict(id=p['id']))
        assert can_send(tx, p['id'], 'e3', 'run-new')

def test_campaign_link_roundtrip(make_person):
    p = make_person(name='Link')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e3', 'https://ahavah.app/discover', p['id'])
        key = url.rsplit('/', 1)[1]
        assert record_click(tx, key, 'Mozilla/5.0') == 'https://ahavah.app/discover'
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s", dict(k=key)).fetchone()['n']
        assert n == 1
        assert record_click(tx, 'nope', 'x') is None

def test_click_route_redirects(client, make_person):
    p = make_person(name='Route')
    with api_tx() as tx:
        url = make_campaign_link(tx, 'e1', 'https://ahavah.app/discover', p['id'])
    key = url.rsplit('/', 1)[1]
    r = client.get(f'/s/{key}')
    assert r.status_code == 302 and r.headers['Location'] == 'https://ahavah.app/discover'
    assert client.get('/s/doesnotexist').status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, `service.campaigns` not found.

- [ ] **Step 3: Implement the module**

```python
# service/campaigns/__init__.py
"""Campaign email send log, per-member frequency cap, and click links.

Spec 3.5: at most one campaign email per member per 7 days (E4/E5 are
member-triggered and exempt); each send carries a campaign_id so a
retried request cannot send twice. Spec 3.4: every CTA is a /s/<key>
link that counts clicks."""
from __future__ import annotations

import secrets
from typing import Optional

from service.config import WEB_BASE_URL

_Q_SAME_RUN = """
    SELECT 1 FROM email_send_log
     WHERE person_id = %(pid)s AND campaign = %(c)s AND campaign_id = %(cid)s
"""
_Q_RECENT = """
    SELECT 1 FROM email_send_log
     WHERE person_id = %(pid)s AND sent_at > NOW() - make_interval(days => %(d)s)
     LIMIT 1
"""

def can_send(tx, person_id: int, campaign: str, campaign_id: str, *,
             cap_days: int = 7, exempt: bool = False) -> bool:
    if tx.execute(_Q_SAME_RUN, dict(pid=person_id, c=campaign, cid=campaign_id)).fetchone():
        return False
    if exempt:
        return True
    return tx.execute(_Q_RECENT, dict(pid=person_id, d=cap_days)).fetchone() is None

def log_send(tx, person_id: int, campaign: str, campaign_id: str,
             message_id: Optional[str]) -> None:
    tx.execute(
        """
        INSERT INTO email_send_log (person_id, campaign, campaign_id, message_id)
        VALUES (%(pid)s, %(c)s, %(cid)s, %(mid)s)
        ON CONFLICT (person_id, campaign, campaign_id) DO NOTHING
        """,
        dict(pid=person_id, c=campaign, cid=campaign_id, mid=message_id))

def make_campaign_link(tx, kind: str, target_url: str,
                       subject_person_id: Optional[int] = None) -> str:
    key = secrets.token_urlsafe(6)
    tx.execute(
        """
        INSERT INTO campaign_link (key, kind, target_url, subject_person_id)
        VALUES (%(k)s, %(kind)s, %(url)s, %(pid)s)
        """,
        dict(k=key, kind=kind, url=target_url, pid=subject_person_id))
    return f"{WEB_BASE_URL.rstrip('/')}/s/{key}"

def _ua_class(ua: str) -> str:
    u = (ua or '').lower()
    if 'facebookexternalhit' in u or 'bot' in u or 'crawler' in u:
        return 'bot'
    if 'mobile' in u or 'android' in u or 'iphone' in u:
        return 'mobile'
    return 'desktop' if u else 'unknown'

def record_click(tx, key: str, user_agent: str) -> Optional[str]:
    row = tx.execute("SELECT target_url FROM campaign_link WHERE key = %(k)s",
                     dict(k=key)).fetchone()
    if not row:
        return None
    tx.execute("INSERT INTO campaign_click (link_key, ua_class) VALUES (%(k)s, %(u)s)",
               dict(k=key, u=_ua_class(user_agent)))
    return row['target_url']
```

```python
# service/api/campaign_link_routes.py
"""GET /s/<key>: count a campaign click and redirect. Read-only for the
member; the only write is the click row, which is why GET is acceptable
here (spec: action links that change member state must POST)."""
from __future__ import annotations

from flask import abort, redirect, request

from database import api_tx
from service.api.decorators import get
from service.campaigns import record_click

@get('/s/<key>')
def get_campaign_link(key: str):
    with api_tx() as tx:
        target = record_click(tx, key, request.headers.get('User-Agent', ''))
    if not target:
        abort(404)
    return redirect(target, code=302)
```

Check `service/api/decorators.py` for the exact name of the unauthenticated GET decorator (`get`) and its limiter argument; mirror `unsubscribe_routes.py`.

- [ ] **Step 4: Run the tests**

Expected: PASS, four tests.

- [ ] **Step 5: Commit (hold)**

```bash
git add service/campaigns/__init__.py service/api/campaign_link_routes.py service/api/__init__.py tests/test_campaigns.py
git commit -m "feat(campaigns): send log, frequency cap, and click links"
```

---

### Task 5: Web route `/s/[key]` that forwards to the API redirect

**Files:**
- Create: `d:/Antigravity/ahavah-web/src/app/s/[key]/route.ts`
- Test: `d:/Antigravity/ahavah-web/tests/app/s-route.test.ts`

**Interfaces:**
- Consumes: API `GET /s/<key>` (Task 4).
- Produces: `https://ahavah.app/s/<key>` resolving for email clients and social captions; the same-origin `/api` proxy already forwards to the API, so the route only needs to 307 to `/api/s/<key>`.

- [ ] **Step 1: Write the failing test**

```ts
// tests/app/s-route.test.ts
import { describe, it, expect } from "vitest";
import { GET } from "@/app/s/[key]/route";

describe("/s/[key]", () => {
  it("forwards to the API click endpoint on the same origin", async () => {
    const res = await GET(new Request("https://ahavah.app/s/abc123"), { params: Promise.resolve({ key: "abc123" }) });
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("https://ahavah.app/api/s/abc123");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test -- tests/app/s-route.test.ts`. Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```ts
// src/app/s/[key]/route.ts
// Campaign click links. The API counts the click and redirects; this
// route only hops to the same-origin proxy so the link works from any
// email client or social caption. Read node_modules/next/dist/docs for
// the Route Handler signature before changing it.
import { NextResponse } from "next/server";

export async function GET(req: Request, ctx: { params: Promise<{ key: string }> }) {
  const { key } = await ctx.params;
  const url = new URL(req.url);
  return NextResponse.redirect(`${url.origin}/api/s/${encodeURIComponent(key)}`, 307);
}
```

Confirm `src/proxy.ts` (the prelaunch middleware) does not gate `/s/*` behind the `ahavah.authed` cookie; add `/s/` to its public allowlist if it does.

- [ ] **Step 4: Run tests and gates**

Run: `pnpm test -- tests/app/s-route.test.ts && npx tsc --noEmit && npx eslint src/app/s --max-warnings=0`. Expected: PASS.

- [ ] **Step 5: Commit (hold)**

```bash
git add src/app/s tests/app/s-route.test.ts src/proxy.ts
git commit -m "feat(links): /s/<key> campaign click forwarder"
```

---

### Task 6: `service/growth` queries and `GET /admin/growth/stats`

**Files:**
- Create: `service/growth/__init__.py`
- Create: `service/growth/queries.py`
- Create: `service/api/admin/growth_routes.py`
- Modify: `service/api/__init__.py` (add `import service.api.admin.growth_routes  # noqa: E402,F401` after `audit_routes`)
- Test: `tests/test_growth_queries.py`

**Interfaces:**
- Produces:
  - `growth_stats(tx) -> dict` with keys `members_by_gender` (list of `{gender, members, new_7d, new_30d, acted_14d, stale_30d, never_acted, with_photo, premium, opted_in}`), `matches`, `matches_30d`, `likes_total`, `likes_7d`, `msgs_7d`, `msgs_30d`, `opted_in`.
  - `newcomers_since(tx, person_id: int, since, limit: int = 5) -> list[dict]` rows `{first_name, country, joined_at}` of activated members of the genders the member is looking for (`other_peoples_genders` via `person.gender_id`/preference tables), inside the member's age preference when `search_preference_age` has a row, joined after `since`, excluding the member and admin.
  - `dormant_cohort(tx, days: int = 30, resend_days: int = 30) -> list[dict]` rows `{person_id, email, name, last_action}` per spec E3.
  - `last_action_at(tx, person_id) -> datetime | None`.
  - HTTP `GET /admin/growth/stats` (admin) returning `growth_stats`.
- Test accounts are excluded through `EXCLUDED_EMAILS = ('admin@ahavah.app',)` plus the env list `AHAVAH_TEST_ACCOUNT_EMAILS` (comma separated).

- [ ] **Step 1: Confirm the preference tables**

Run in the test stack: `SELECT table_name FROM information_schema.tables WHERE table_name IN ('search_preference_age','search_preference_gender');` Expected: both present (Duolicious upstream). If `search_preference_gender` is absent, use `person.other_peoples_genders`? No such column exists; the gender preference is in `search_preference_gender(person_id, gender_id)`. Record which exist in the pre-flight file.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_growth_queries.py
from datetime import datetime, timedelta, timezone
from database import api_tx
from service.growth.queries import growth_stats, newcomers_since, dormant_cohort, last_action_at

def _like(tx, liker, liked, days_ago):
    tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - make_interval(days => %(d)s))",
               dict(a=liker, b=liked, d=days_ago))

def _prefers(tx, pid, gender_name):
    tx.execute("INSERT INTO search_preference_gender (person_id, gender_id) SELECT %(p)s, id FROM gender WHERE name = %(g)s ON CONFLICT DO NOTHING",
               dict(p=pid, g=gender_name))

def test_stats_shape_and_exclusions(make_person):
    make_person(name='A', gender='Man'); make_person(name='B', gender='Woman')
    with api_tx('read committed') as tx:
        s = growth_stats(tx)
    assert {'members_by_gender', 'matches', 'likes_7d', 'msgs_30d', 'opted_in'} <= set(s)
    genders = {r['gender'] for r in s['members_by_gender']}
    assert genders <= {'Man', 'Woman'}

def test_dormant_cohort_edges(make_person):
    active = make_person(name='Active', gender='Man')
    stale = make_person(name='Stale', gender='Man')
    resent = make_person(name='Resent', gender='Man')
    other = make_person(name='Other', gender='Woman')
    with api_tx() as tx:
        _like(tx, active['id'], other['id'], 3)
        _like(tx, stale['id'], other['id'], 31)
        _like(tx, resent['id'], other['id'], 40)
        tx.execute("UPDATE person SET reinvite_sent_at = NOW() - interval '10 days' WHERE id = %(id)s", dict(id=resent['id']))
        ids = {r['person_id'] for r in dormant_cohort(tx, days=30, resend_days=30)}
    assert stale['id'] in ids
    assert active['id'] not in ids
    assert resent['id'] not in ids

def test_newcomers_respect_gender_preference_and_since(make_person):
    me = make_person(name='Me', gender='Man')
    new_w = make_person(name='Rivka', gender='Woman')
    new_m = make_person(name='Dan', gender='Man')
    with api_tx() as tx:
        _prefers(tx, me['id'], 'Woman')
        since = datetime.now(timezone.utc) - timedelta(days=1)
        rows = newcomers_since(tx, me['id'], since)
    names = [r['first_name'] for r in rows]
    assert 'Rivka' in names and 'Dan' not in names

def test_last_action_none_for_untouched(make_person):
    p = make_person(name='Quiet')
    with api_tx('read committed') as tx:
        assert last_action_at(tx, p['id']) is None
```

- [ ] **Step 3: Run to verify they fail**

Expected: FAIL, `service.growth` not found.

- [ ] **Step 4: Implement the queries**

```python
# service/growth/__init__.py
"""Growth: one source of member statistics and cohorts for the admin
Growth tab and the campaign emails (spec 3.8.1)."""
```

```python
# service/growth/queries.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

EXCLUDED_EMAILS = ('admin@ahavah.app',)

def _excluded() -> list[str]:
    extra = [e.strip().lower() for e in os.environ.get('AHAVAH_TEST_ACCOUNT_EMAILS', '').split(',') if e.strip()]
    return [*EXCLUDED_EMAILS, *extra]

_Q_LAST_ACTION = """
    SELECT GREATEST(
        COALESCE((SELECT max(created_at) FROM liked    WHERE liker_id = %(pid)s), to_timestamp(0)),
        COALESCE((SELECT max(created_at) FROM skipped  WHERE subject_person_id = %(pid)s), to_timestamp(0)),
        COALESCE((SELECT max(created_at) FROM messaged WHERE subject_person_id = %(pid)s), to_timestamp(0))
    ) AS last_action
"""

def last_action_at(tx, person_id: int) -> Optional[datetime]:
    row = tx.execute(_Q_LAST_ACTION, dict(pid=person_id)).fetchone()
    la = row['last_action'] if row else None
    return None if la is None or la.timestamp() == 0 else la

_Q_STATS_BY_GENDER = """
    WITH act AS (
      SELECT p.id, g.name AS gender, p.sign_up_time, p.spotlight_opt_in,
             p.subscription_expires_at,
             EXISTS (SELECT 1 FROM photo ph WHERE ph.person_id = p.id) AS has_photo,
             GREATEST(
               COALESCE((SELECT max(created_at) FROM liked    l WHERE l.liker_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM skipped  s WHERE s.subject_person_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM messaged m WHERE m.subject_person_id = p.id), to_timestamp(0))
             ) AS last_action
        FROM person p JOIN gender g ON g.id = p.gender_id
       WHERE p.activated AND lower(p.email) <> ALL(%(ex)s)
    )
    SELECT gender,
           count(*)                                                              AS members,
           count(*) FILTER (WHERE sign_up_time > NOW() - interval '7 days')      AS new_7d,
           count(*) FILTER (WHERE sign_up_time > NOW() - interval '30 days')     AS new_30d,
           count(*) FILTER (WHERE last_action > NOW() - interval '14 days')      AS acted_14d,
           count(*) FILTER (WHERE last_action < NOW() - interval '30 days' AND last_action > to_timestamp(0)) AS stale_30d,
           count(*) FILTER (WHERE last_action = to_timestamp(0))                 AS never_acted,
           count(*) FILTER (WHERE has_photo)                                     AS with_photo,
           count(*) FILTER (WHERE subscription_expires_at > NOW())               AS premium,
           count(*) FILTER (WHERE spotlight_opt_in)                              AS opted_in
      FROM act GROUP BY gender ORDER BY gender
"""

_Q_TOTALS = """
    SELECT (SELECT count(*) FROM ahavah_match) AS matches,
           (SELECT count(*) FROM ahavah_match WHERE created_at > NOW() - interval '30 days') AS matches_30d,
           (SELECT count(*) FROM liked) AS likes_total,
           (SELECT count(*) FROM liked WHERE created_at > NOW() - interval '7 days') AS likes_7d,
           (SELECT count(*) FROM messaged WHERE created_at > NOW() - interval '7 days') AS msgs_7d,
           (SELECT count(*) FROM messaged WHERE created_at > NOW() - interval '30 days') AS msgs_30d,
           (SELECT count(*) FROM person WHERE activated AND spotlight_opt_in) AS opted_in
"""

def growth_stats(tx) -> dict:
    by_gender = tx.execute(_Q_STATS_BY_GENDER, dict(ex=_excluded())).fetchall()
    totals = tx.execute(_Q_TOTALS).fetchone()
    return {'members_by_gender': [dict(r) for r in by_gender], **dict(totals)}

_Q_DORMANT = """
    WITH act AS (
      SELECT p.id, p.email, p.name, p.reinvite_sent_at,
             GREATEST(
               COALESCE((SELECT max(created_at) FROM liked    l WHERE l.liker_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM skipped  s WHERE s.subject_person_id = p.id), to_timestamp(0)),
               COALESCE((SELECT max(created_at) FROM messaged m WHERE m.subject_person_id = p.id), to_timestamp(0))
             ) AS last_action
        FROM person p
       WHERE p.activated AND p.deletion_requested_at IS NULL
         AND lower(p.email) <> ALL(%(ex)s)
    )
    SELECT id AS person_id, email, name, last_action
      FROM act
     WHERE last_action > to_timestamp(0)
       AND last_action < NOW() - make_interval(days => %(days)s)
       AND (reinvite_sent_at IS NULL OR reinvite_sent_at < NOW() - make_interval(days => %(resend)s))
     ORDER BY last_action
"""

def dormant_cohort(tx, days: int = 30, resend_days: int = 30) -> list[dict]:
    return [dict(r) for r in tx.execute(_Q_DORMANT, dict(days=days, resend=resend_days, ex=_excluded())).fetchall()]

_Q_NEWCOMERS = """
    SELECT split_part(p.name, ' ', 1) AS first_name, p.country, p.sign_up_time AS joined_at
      FROM person p
     WHERE p.activated AND p.id <> %(pid)s
       AND lower(p.email) <> ALL(%(ex)s)
       AND p.sign_up_time > %(since)s
       AND p.gender_id IN (SELECT gender_id FROM search_preference_gender WHERE person_id = %(pid)s)
       AND (
         NOT EXISTS (SELECT 1 FROM search_preference_age a WHERE a.person_id = %(pid)s)
         OR EXISTS (
           SELECT 1 FROM search_preference_age a
            WHERE a.person_id = %(pid)s
              AND date_part('year', age(p.date_of_birth)) BETWEEN COALESCE(a.min_age, 18) AND COALESCE(a.max_age, 120)
         )
       )
     ORDER BY p.sign_up_time DESC
     LIMIT %(lim)s
"""

def newcomers_since(tx, person_id: int, since: datetime, limit: int = 5) -> list[dict]:
    return [dict(r) for r in tx.execute(_Q_NEWCOMERS, dict(pid=person_id, since=since, lim=limit, ex=_excluded())).fetchall()]
```

If Step 1 showed a different preference schema, adjust the two subqueries and record the change in the pre-flight file. Country is not a persisted filter today (the Discover filters live on the client), so the spec's country filter is deliberately not applied; the age and gender preferences are.

```python
# service/api/admin/growth_routes.py
from __future__ import annotations

import duotypes as t
from database import api_tx
from service.admin import require_admin
from service.api.decorators import aget
from service.growth.queries import growth_stats

@aget('/admin/growth/stats')
def get_admin_growth_stats(s: t.SessionInfo):
    require_admin(s)
    with api_tx('read committed') as tx:
        return growth_stats(tx)
```

- [ ] **Step 5: Run the tests**

Expected: PASS, four tests. Also run the full suite: 235 baseline plus the new tests, all green.

- [ ] **Step 6: Commit (hold)**

```bash
git add service/growth service/api/admin/growth_routes.py service/api/__init__.py tests/test_growth_queries.py
git commit -m "feat(growth): shared member stats and cohort queries with admin stats endpoint"
```

---

### Task 7: Spotlight consent: API write path, signed confirm page, privacy switch

**Files:**
- Modify: `duotypes/__init__.py` (`PatchProfileInfo`, after `ahavah_extra` at line 713: `spotlight_opt_in: Optional[bool] = None`)
- Modify: `service/person/__init__.py` (`patch_profile_info`, the field loop; add the branch below beside the `verification_required` handling)
- Modify: `service/unsubscribe/__init__.py` (`_SCOPES` gains `"spotlight"`)
- Create: `service/api/spotlight_routes.py`
- Modify: `service/api/__init__.py` (import the new routes next to `unsubscribe_routes`)
- Modify: `d:/Antigravity/ahavah-web/src/app/settings/privacy/page.tsx` (new switch row from the SOT, wired like `requireVerifiedMatches`)
- Modify: `d:/Antigravity/ahavah-web/src/lib/use-profile.ts` (read `spotlight_opt_in` from `/profile-info` into `profile.spotlightOptIn`; add the server key to `Q_GET_PROFILE_INFO` in `service/person/sql/__init__.py` as `'spotlight_opt_in', (SELECT spotlight_opt_in FROM person WHERE id = %(person_id)s)` if the 50-pair cap allows, otherwise inside the `ahavah_extra` merge exactly as `country` was merged in `98a6360`)
- Create: `d:/Antigravity/ahavah-web/src/app/spotlight/confirm/[token]/page.tsx` (transcribed from the SOT; a form whose button POSTs to `/api/spotlight/confirm/<token>`)
- Test: `tests/test_spotlight_consent.py`, `d:/Antigravity/ahavah-web/tests/lib/spotlight-confirm.test.tsx`

**Interfaces:**
- Consumes: `make_token`/`parse_token` from `service.unsubscribe` (Task 3 columns).
- Produces:
  - `PATCH /profile-info {"spotlight_opt_in": true|false}` sets `spotlight_opt_in` and stamps `spotlight_opt_in_at` on true.
  - `GET /spotlight/confirm/<token>` returns 200 JSON `{"email_masked": "...", "already": bool}` and changes nothing.
  - `POST /spotlight/confirm/<token>` sets opt-in true for the token's email, 200 `{"ok": true}`; invalid or unknown token 400; expired token (older than 30 days, encoded as a `ts` segment) 410.
  - `set_spotlight_opt_in(tx, person_id: int, value: bool) -> None` used by Phase B's opt-out cancellation hook.
  - `spotlight_confirm_url(email: str) -> str` for E1.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spotlight_consent.py
from database import api_tx
from service.spotlight import set_spotlight_opt_in, spotlight_confirm_url, make_confirm_token

def _flag(pid):
    with api_tx('read committed') as tx:
        return tx.execute("SELECT spotlight_opt_in, spotlight_opt_in_at FROM person WHERE id = %(id)s", dict(id=pid)).fetchone()

def test_set_opt_in_stamps_time(make_person):
    p = make_person(name='Opt')
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], True)
    row = _flag(p['id'])
    assert row['spotlight_opt_in'] is True and row['spotlight_opt_in_at'] is not None
    with api_tx() as tx:
        set_spotlight_opt_in(tx, p['id'], False)
    assert _flag(p['id'])['spotlight_opt_in'] is False

def test_get_confirm_changes_nothing_and_post_opts_in(client, make_person):
    p = make_person(name='Link')
    with api_tx('read committed') as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    token = make_confirm_token(email)
    r = client.get(f'/spotlight/confirm/{token}')
    assert r.status_code == 200 and r.get_json()['already'] is False
    assert _flag(p['id'])['spotlight_opt_in'] is False          # GET never mutates
    r = client.post(f'/spotlight/confirm/{token}')
    assert r.status_code == 200 and _flag(p['id'])['spotlight_opt_in'] is True
    assert client.post(f'/spotlight/confirm/{token}').status_code == 200  # idempotent
    assert client.post('/spotlight/confirm/not.a.token').status_code == 400

def test_expired_token_is_rejected(client, make_person, monkeypatch):
    import service.spotlight as sp
    p = make_person(name='Old')
    with api_tx('read committed') as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    monkeypatch.setattr(sp, '_now_ts', lambda: 0)          # token minted at epoch
    token = make_confirm_token(email)
    monkeypatch.undo()
    assert client.post(f'/spotlight/confirm/{token}').status_code == 410

def test_confirm_url_shape():
    assert spotlight_confirm_url('a@b.co').startswith('https://') and '/spotlight/confirm/' in spotlight_confirm_url('a@b.co')
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, `service.spotlight` not found.

- [ ] **Step 3: Implement**

```python
# service/spotlight/__init__.py
"""Spotlight consent (spec 3.1). Tokens reuse the unsubscribe HMAC with a
minted-at timestamp so links expire after 30 days. GET never mutates."""
from __future__ import annotations

import base64, hashlib, hmac, time
from urllib.parse import quote

from service.config import WEB_BASE_URL
from service.unsubscribe import _secret  # same key, fails closed when unset

TOKEN_TTL_SECONDS = 30 * 24 * 3600

def _now_ts() -> int:
    return int(time.time())

def _sign(email: str, ts: int) -> str:
    payload = f"spotlight|{email}|{ts}".encode()
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode().rstrip('=')

def make_confirm_token(email: str) -> str:
    norm = email.strip().lower()
    ts = _now_ts()
    e = base64.urlsafe_b64encode(norm.encode()).decode().rstrip('=')
    return f"{e}.{ts}.{_sign(norm, ts)}"

def parse_confirm_token(token: str) -> tuple[str | None, str]:
    """Returns (email, status) where status is 'ok', 'invalid' or 'expired'."""
    parts = (token or '').split('.')
    if len(parts) != 3:
        return None, 'invalid'
    e, ts_s, sig = parts
    try:
        email = base64.urlsafe_b64decode(e + '=' * (-len(e) % 4)).decode()
        ts = int(ts_s)
    except Exception:
        return None, 'invalid'
    if not hmac.compare_digest(_sign(email, ts), sig):
        return None, 'invalid'
    if _now_ts() - ts > TOKEN_TTL_SECONDS:
        return None, 'expired'
    return email, 'ok'

def spotlight_confirm_url(email: str) -> str:
    return f"{WEB_BASE_URL.rstrip('/')}/spotlight/confirm/{quote(make_confirm_token(email))}"

def set_spotlight_opt_in(tx, person_id: int, value: bool) -> None:
    tx.execute(
        """
        UPDATE person
           SET spotlight_opt_in = %(v)s,
               spotlight_opt_in_at = CASE WHEN %(v)s THEN NOW() ELSE spotlight_opt_in_at END
         WHERE id = %(id)s
        """,
        dict(v=value, id=person_id))
    # Phase B hook: cancel queued cards for this member on opt-out.

def set_spotlight_opt_in_by_email(tx, email: str, value: bool) -> bool:
    row = tx.execute("SELECT id FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email.lower())).fetchone()
    if not row:
        return False
    set_spotlight_opt_in(tx, row['id'], value)
    return True
```

```python
# service/api/spotlight_routes.py
from __future__ import annotations

from flask import abort, jsonify

from database import api_tx
from emails.base import mask_email
from service.api.decorators import get, post
from service.spotlight import parse_confirm_token, set_spotlight_opt_in_by_email

def _resolve(token: str) -> str:
    email, status = parse_confirm_token(token)
    if status == 'expired':
        abort(410)
    if status != 'ok' or not email:
        abort(400)
    return email

@get('/spotlight/confirm/<token>')
def get_spotlight_confirm(token: str):
    email = _resolve(token)
    with api_tx('read committed') as tx:
        row = tx.execute("SELECT spotlight_opt_in FROM person WHERE lower(email) = %(e)s AND activated", dict(e=email)).fetchone()
    if not row:
        abort(400)
    return jsonify(email_masked=mask_email(email), already=bool(row['spotlight_opt_in']))

@post('/spotlight/confirm/<token>')
def post_spotlight_confirm(token: str):
    email = _resolve(token)
    with api_tx() as tx:
        if not set_spotlight_opt_in_by_email(tx, email, True):
            abort(400)
    return jsonify(ok=True)
```

In `service/person/__init__.py` `patch_profile_info`, beside the existing `verification_required` branch, add:

```python
        if req.spotlight_opt_in is not None:
            from service.spotlight import set_spotlight_opt_in
            set_spotlight_opt_in(tx, person_id, bool(req.spotlight_opt_in))
```

Use the same `tx` and `person_id` variables that branch uses. Mirror the rate limiters used in `unsubscribe_routes.py` on both new routes.

Web side: transcribe the SOT switch row into the privacy page. Extend `BackedKey` with `spotlightOptIn`, map it to the server key `spotlight_opt_in`, and read it in `use-profile.ts` from the merged profile as `spotlightOptIn`. Transcribe the SOT confirmation page into `src/app/spotlight/confirm/[token]/page.tsx`: on load it fetches `/api/spotlight/confirm/<token>` to show the masked email and the already-opted state; the single button submits a `fetch(..., { method: "POST" })` and swaps to the success state; 400 and 410 render the SOT's invalid and expired states. Add `/spotlight/` to the public allowlist in `src/proxy.ts`.

```tsx
// tests/lib/spotlight-confirm.test.tsx (vitest + testing-library, matches privacy-save.test.tsx setup)
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, it, expect } from "vitest";
import ConfirmPage from "@/app/spotlight/confirm/[token]/page";

it("does not opt in on load and POSTs on the button", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    return new Response(JSON.stringify({ email_masked: "a***@b.co", already: false, ok: true }), { status: 200 });
  }));
  render(<ConfirmPage params={Promise.resolve({ token: "tok" })} />);
  await screen.findByText(/a\*\*\*@b\.co/);
  expect(calls).toEqual(["GET /api/spotlight/confirm/tok"]);
  fireEvent.click(screen.getByRole("button", { name: /feature me/i }));
  await waitFor(() => expect(calls).toContain("POST /api/spotlight/confirm/tok"));
});
```

- [ ] **Step 4: Run tests and gates**

API: scoped then full suite. Web: `pnpm test && npx tsc --noEmit && npx eslint src/app/spotlight src/app/settings/privacy/page.tsx src/lib/use-profile.ts --max-warnings=0`. Render-verify the confirm page and the switch row at 390 dark and light with the invite harness pattern (`scripts/verify-invite.mjs` as the template) against the SOT frames.

- [ ] **Step 5: Commit (hold, both repos)**

```bash
cd /d/Antigravity/ahavah-api && git add duotypes/__init__.py service/person/__init__.py service/person/sql/__init__.py service/unsubscribe/__init__.py service/spotlight service/api/spotlight_routes.py service/api/__init__.py tests/test_spotlight_consent.py && git commit -m "feat(spotlight): consent columns write path and signed confirm endpoints"
cd /d/Antigravity/ahavah-web && git add src/app/spotlight src/app/settings/privacy/page.tsx src/lib/use-profile.ts src/proxy.ts tests/lib/spotlight-confirm.test.tsx && git commit -m "feat(spotlight): privacy switch and confirmation page"
```

---

### Task 8: E1 announcement email (module, admin send endpoint, CLI)

**Files:**
- Create: `emails/spotlight_announcement.py`
- Create: `emails/send_spotlight_announcement.py`
- Create: `service/campaigns/runner.py`
- Modify: `service/api/admin/growth_routes.py` (add the emails endpoints)
- Test: `tests/test_spotlight_announcement.py`, `tests/test_campaign_runner.py`

**Interfaces:**
- Consumes: `spotlight_confirm_url` (Task 7), `can_send`/`log_send`/`make_campaign_link` (Task 4), `emails.base` helpers, `make_aws_smtp().send(subject=, body=, to_addr=, from_addr=, list_unsubscribe=)`.
- Produces:
  - `spotlight_announcement_html(confirm_url: str, settings_url: str, unsubscribe_url: str) -> str`
  - `run_campaign(tx_factory, campaign: str, campaign_id: str, recipients: list[dict], build: Callable[[dict], tuple[str, str]], *, send: bool, cap_days: int = 7, exempt: bool = False) -> dict` returning `{sent, skipped_cap, skipped_suppressed, dry_run: bool, campaign_id}`; `build(row)` returns `(subject, html)`; `recipients` rows carry `person_id`, `email`, `name`.
  - HTTP (admin): `GET /admin/growth/emails` listing the three campaigns with `recipients`, `last_sent_at`, `last_campaign_id`; `POST /admin/growth/emails/<campaign>/preview {"to": email}` sends one preview to the given admin address; `POST /admin/growth/emails/<campaign>/send {"campaign_id": str, "dry_run": bool}` returns the runner result and writes `record_audit(tx, s, 'growth.email.send', metadata={campaign, campaign_id, sent})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spotlight_announcement.py
from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT

def test_announcement_is_on_template_and_has_no_em_dash():
    html = spotlight_announcement_html('https://ahavah.app/spotlight/confirm/t', 'https://ahavah.app/settings/privacy', 'https://ahavah.app/u/x')
    assert 'title-spotlight.png' in html and 'title-spotlight-wht.png' in html
    assert 'https://ahavah.app/spotlight/confirm/t' in html
    assert 'Unsubscribe' in html
    assert '\u2014' not in html and '\u2014' not in SUBJECT
    assert 'first name, age, country' in html.lower()
```

```python
# tests/test_campaign_runner.py
from database import api_tx
from service.campaigns.runner import run_campaign

class _Smtp:
    def __init__(self): self.sent = []
    def send(self, **kw): self.sent.append(kw['to_addr']); return 'mid-' + str(len(self.sent))

def test_runner_dry_run_sends_nothing_and_logs_nothing(make_person, monkeypatch):
    import service.campaigns.runner as r
    smtp = _Smtp(); monkeypatch.setattr(r, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='Dry')
    with api_tx('read committed') as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    res = run_campaign(api_tx, 'e1', 'run-dry', [dict(person_id=p['id'], email=email, name='Dry')],
                       lambda row: ('Subj', '<p>hi</p>'), send=False)
    assert res['dry_run'] and res['sent'] == 1 and smtp.sent == []
    with api_tx('read committed') as tx:
        assert tx.execute("SELECT count(*) AS n FROM email_send_log WHERE person_id = %(id)s", dict(id=p['id'])).fetchone()['n'] == 0

def test_runner_sends_once_per_campaign_id(make_person, monkeypatch):
    import service.campaigns.runner as r
    smtp = _Smtp(); monkeypatch.setattr(r, 'make_aws_smtp', lambda: smtp)
    p = make_person(name='Once')
    with api_tx('read committed') as tx:
        email = tx.execute("SELECT email FROM person WHERE id = %(id)s", dict(id=p['id'])).fetchone()['email']
    rows = [dict(person_id=p['id'], email=email, name='Once')]
    a = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True)
    b = run_campaign(api_tx, 'e1', 'run-1', rows, lambda row: ('S', '<p>x</p>'), send=True)
    assert a['sent'] == 1 and b['sent'] == 0 and len(smtp.sent) == 1
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, modules not found.

- [ ] **Step 3: Implement**

```python
# emails/spotlight_announcement.py
"""E1 Spotlight announcement + opt-in (spec 3.5). Canonical shell.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "Meet Spotlight, a new way to be seen on Ahavah"

def spotlight_announcement_html(confirm_url: str, settings_url: str, unsubscribe_url: str) -> str:
    body = f"""
{chip("New on Ahavah")}

{title_image("title-spotlight.png", "title-spotlight-wht.png", "Meet Spotlight.", 430)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  Spotlight puts real members in front of the wider community: a welcome
  when you join, a member of the week, and the occasional highlight, on
  the Ahavah Facebook page and Instagram and in the weekly community email.
</p>

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  It is opt in. If you join, we share only your first name, age, country
  and one photo you choose, and you approve every card before it goes out.
  You can turn it off any time in settings.
</p>

{button("Feature me in Spotlight", confirm_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Not for you? Nothing changes. Your profile stays exactly as private as it is today.")}

<p class="e-text" style="margin:16px 0 0;font-family:{SANS};font-size:15px;line-height:1.5;color:{MUTED};">
  You can also switch it on later under <a href="{settings_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Settings, Privacy</a>.
</p>
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/privacy" style="color:{MUTED};font-weight:600;text-decoration:underline;">Privacy</a>
</div>
"""
    return render(title=SUBJECT, preheader="Opt in to be featured. First name, age, country, one photo you choose.", body_html=body, footer_html=footer)
```

```python
# service/campaigns/runner.py
"""One send loop for every campaign email: suppression, per-member cap,
per-run idempotency, send log. Dry run builds every message and sends none."""
from __future__ import annotations

from typing import Callable

from emails.base import is_suppressed_send, mask_email
from service.campaigns import can_send, log_send
from smtp import make_aws_smtp

FROM_ADDR_DEFAULT = None  # each build() decides; runner uses support@ via the email module constant

def run_campaign(tx_factory, campaign: str, campaign_id: str, recipients: list[dict],
                 build: Callable[[dict], tuple[str, str]], *, send: bool,
                 from_addr: str, list_unsubscribe: Callable[[str], str] | None = None,
                 cap_days: int = 7, exempt: bool = False) -> dict:
    smtp = make_aws_smtp() if send else None
    sent = skipped_cap = skipped_suppressed = 0
    for row in recipients:
        email = row['email']
        if is_suppressed_send(email):
            skipped_suppressed += 1
            continue
        with tx_factory() as tx:
            if not can_send(tx, row['person_id'], campaign, campaign_id, cap_days=cap_days, exempt=exempt):
                skipped_cap += 1
                continue
        subject, html = build(row)
        if send:
            mid = smtp.send(subject=subject, body=html, to_addr=email, from_addr=from_addr,
                            list_unsubscribe=list_unsubscribe(email) if list_unsubscribe else None)
            with tx_factory() as tx:
                log_send(tx, row['person_id'], campaign, campaign_id, str(mid) if mid else None)
            print(f"sent {campaign} to {mask_email(email)}")
        else:
            print(f"DRY RUN {campaign} {mask_email(email)}")
        sent += 1
    return dict(sent=sent, skipped_cap=skipped_cap, skipped_suppressed=skipped_suppressed,
                dry_run=not send, campaign_id=campaign_id)
```

Adjust the test calls to pass `from_addr='support@ahavah.app'`. Check `smtp.py` for whether `send()` returns a message id; if it returns None, log `None` (the log row still guarantees idempotency).

```python
# emails/send_spotlight_announcement.py
"""python -m emails.send_spotlight_announcement [--send] [--campaign-id ID] [--preview you@x]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.spotlight_announcement import spotlight_announcement_html, SUBJECT, FROM_ADDR
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.spotlight import spotlight_confirm_url
from service.unsubscribe import make_url as _unsub_url

_Q_RECIPIENTS = """
    SELECT id AS person_id, email, name FROM person
     WHERE activated AND deletion_requested_at IS NULL AND lower(email) <> ALL(%(ex)s)
     ORDER BY id
"""

def build_for(row: dict) -> tuple[str, str]:
    return SUBJECT, spotlight_announcement_html(
        spotlight_confirm_url(row['email']),
        f"{WEB_BASE_URL}/settings/privacy",
        _unsub_url('notifications', row['email'], WEB_BASE_URL))

def recipients() -> list[dict]:
    with api_tx('read committed') as tx:
        return [dict(r) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded())).fetchall()]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true')
    ap.add_argument('--campaign-id', default=None)
    ap.add_argument('--preview', default=None)
    a = ap.parse_args()
    if a.preview:
        subject, html = build_for(dict(person_id=0, email=a.preview, name='Preview'))
        from smtp import make_aws_smtp
        make_aws_smtp().send(subject=subject, body=html, to_addr=a.preview, from_addr=FROM_ADDR)
        print(f"preview sent to {a.preview}"); return
    cid = a.campaign_id or f"e1-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e1', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url('notifications', e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
```

Admin endpoints, appended to `service/api/admin/growth_routes.py`:

```python
from flask import abort, request
from service.admin import record_audit
from service.api.decorators import apost
import emails.send_spotlight_announcement as e1

_CAMPAIGNS = {'e1': e1}   # e2 and e3 are registered by Tasks 9 and 10

@aget('/admin/growth/emails')
def get_admin_growth_emails(s: t.SessionInfo):
    require_admin(s)
    out = []
    with api_tx('read committed') as tx:
        for key, mod in _CAMPAIGNS.items():
            last = tx.execute(
                "SELECT max(sent_at) AS at, (array_agg(campaign_id ORDER BY sent_at DESC))[1] AS cid FROM email_send_log WHERE campaign = %(c)s",
                dict(c=key)).fetchone()
            out.append(dict(campaign=key, recipients=len(mod.recipients()),
                            last_sent_at=last['at'].isoformat() if last and last['at'] else None,
                            last_campaign_id=last['cid'] if last else None))
    return dict(campaigns=out)

@apost('/admin/growth/emails/<campaign>/preview')
def post_admin_growth_email_preview(s: t.SessionInfo, campaign: str):
    require_admin(s)
    mod = _CAMPAIGNS.get(campaign) or abort(404)
    to = (request.get_json(silent=True) or {}).get('to') or abort(400)
    subject, html = mod.build_for(dict(person_id=0, email=to, name='Preview'))
    from smtp import make_aws_smtp
    make_aws_smtp().send(subject=subject, body=html, to_addr=to, from_addr=mod.FROM_ADDR)
    return dict(ok=True)

@apost('/admin/growth/emails/<campaign>/send')
def post_admin_growth_email_send(s: t.SessionInfo, campaign: str):
    require_admin(s)
    mod = _CAMPAIGNS.get(campaign) or abort(404)
    body = request.get_json(silent=True) or {}
    cid = body.get('campaign_id') or abort(400)
    dry = bool(body.get('dry_run', True))
    from service.campaigns.runner import run_campaign
    from service.unsubscribe import make_url as _unsub_url
    from service.config import WEB_BASE_URL
    res = run_campaign(api_tx, campaign, cid, mod.recipients(), mod.build_for, send=not dry,
                       from_addr=mod.FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url('notifications', e, WEB_BASE_URL)}>")
    with api_tx() as tx:
        record_audit(tx, s, 'growth.email.send', metadata=dict(campaign=campaign, campaign_id=cid, dry_run=dry, **res))
    return res
```

Confirm the `apost` decorator passes path parameters after `s` (mirror `users_action_routes.py`).

- [ ] **Step 4: Run tests, then a preview**

Scoped tests then the full suite. Then, from the droplet after deploy (or locally with SES env), `python -m emails.send_spotlight_announcement --preview admin@techbaseltd.com` and inspect the render in Gmail and Apple Mail per `emails/README.md`.

- [ ] **Step 5: Commit (hold)**

```bash
git add emails/spotlight_announcement.py emails/send_spotlight_announcement.py service/campaigns/runner.py service/api/admin/growth_routes.py tests/test_spotlight_announcement.py tests/test_campaign_runner.py
git commit -m "feat(emails): spotlight announcement with campaign runner and admin send endpoints"
```

---

### Task 9: E3 re-invite email

**Files:**
- Create: `emails/reinvite.py`, `emails/send_reinvite.py`
- Modify: `service/api/admin/growth_routes.py` (`_CAMPAIGNS['e3'] = e3`)
- Test: `tests/test_reinvite.py`

**Interfaces:**
- Consumes: `dormant_cohort`, `newcomers_since`, `last_action_at` (Task 6); `make_campaign_link` (Task 4); runner (Task 8).
- Produces: `reinvite_html(first_name: str, newcomers: list[dict], total_new: int, cta_url: str, unsubscribe_url: str) -> str`; `recipients()` returning only cohort members who have at least one newcomer; `build_for(row)`; on a real send, `reinvite_sent_at` stamped inside the same transaction as `log_send` through a `post_send(tx, row)` hook added to the runner (`post_send: Callable[[Any, dict], None] | None = None`, called right after `log_send`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reinvite.py
from datetime import datetime, timedelta, timezone
from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT
from emails.send_reinvite import recipients

def test_reinvite_html_lists_names_and_count():
    html = reinvite_html('Ehud', [dict(first_name='Rivka', country='GB'), dict(first_name='Sarah', country='US')], 4,
                         'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'title-reinvite.png' in html and 'Rivka' in html and 'Sarah' in html and '4 new members' in html
    assert '\u2014' not in html and '\u2014' not in SUBJECT

def test_recipients_require_a_newcomer(make_person):
    stale = make_person(name='Stale', gender='Man')
    other = make_person(name='Other', gender='Woman')
    with api_tx() as tx:
        tx.execute("INSERT INTO liked (liker_id, liked_id, created_at) VALUES (%(a)s, %(b)s, NOW() - interval '31 days')", dict(a=stale['id'], b=other['id']))
        tx.execute("INSERT INTO search_preference_gender (person_id, gender_id) SELECT %(p)s, id FROM gender WHERE name = 'Woman' ON CONFLICT DO NOTHING", dict(p=stale['id']))
    ids = {r['person_id'] for r in recipients()}
    assert stale['id'] in ids            # `other` joined after the stale like (fixture sign_up_time is NOW())
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, modules not found.

- [ ] **Step 3: Implement**

```python
# emails/reinvite.py
"""E3 re-invite for members quiet for 30 days (spec 3.5). Canonical shell.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "New faces on Ahavah since you were here"

def _names_block(newcomers: list[dict]) -> str:
    items = "".join(
        f'<li style="margin:0 0 6px;">{n["first_name"]}'
        + (f' <span style="color:{MUTED};">in {n["country"]}</span>' if n.get('country') else '')
        + '</li>'
        for n in newcomers)
    return f'<ul style="margin:0 0 20px;padding-left:20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{items}</ul>'

def reinvite_html(first_name: str, newcomers: list[dict], total_new: int, cta_url: str, unsubscribe_url: str) -> str:
    noun = "new member" if total_new == 1 else "new members"
    body = f"""
{chip("Since you were away")}

{title_image("title-reinvite.png", "title-reinvite-wht.png", "New faces since you were away.", 486)}

<p class="e-text" style="margin:0 0 16px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  {first_name}, {total_new} {noun} who match what you are looking for have
  joined since you were last here. A few of them:
</p>

{_names_block(newcomers)}

{button("See who joined", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Your profile, matches and messages are exactly as you left them.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving this because you're a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Unsubscribe</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(title=SUBJECT, preheader=f"{total_new} {noun} joined since your last visit.", body_html=body, footer_html=footer)
```

```python
# emails/send_reinvite.py
"""python -m emails.send_reinvite [--send] [--campaign-id ID]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.reinvite import reinvite_html, SUBJECT, FROM_ADDR
from service.campaigns import make_campaign_link
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import dormant_cohort, newcomers_since, _excluded
from service.unsubscribe import make_url as _unsub_url

_Q_TOTAL_NEW = """
    SELECT count(*) AS n FROM person p
     WHERE p.activated AND p.id <> %(pid)s AND lower(p.email) <> ALL(%(ex)s)
       AND p.sign_up_time > %(since)s
       AND p.gender_id IN (SELECT gender_id FROM search_preference_gender WHERE person_id = %(pid)s)
"""

def recipients() -> list[dict]:
    out = []
    with api_tx('read committed') as tx:
        for row in dormant_cohort(tx, days=30, resend_days=30):
            names = newcomers_since(tx, row['person_id'], row['last_action'], limit=5)
            if not names:
                continue
            total = tx.execute(_Q_TOTAL_NEW, dict(pid=row['person_id'], since=row['last_action'], ex=_excluded())).fetchone()['n']
            out.append(dict(**row, newcomers=names, total_new=int(total)))
    return out

def build_for(row: dict) -> tuple[str, str]:
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e3', f"{WEB_BASE_URL}/discover", row['person_id'] or None)
    first = (row.get('name') or 'there').split(' ')[0]
    return SUBJECT, reinvite_html(first, row.get('newcomers', []), row.get('total_new', 0), cta,
                                  _unsub_url('notifications', row['email'], WEB_BASE_URL))

def post_send(tx, row: dict) -> None:
    tx.execute("UPDATE person SET reinvite_sent_at = NOW() WHERE id = %(id)s", dict(id=row['person_id']))

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e3-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e3', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url('notifications', e, WEB_BASE_URL)}>",
                       post_send=post_send))

if __name__ == '__main__':
    main()
```

Add the `post_send` parameter to `run_campaign` in `service/campaigns/runner.py`: signature gains `post_send: Callable[[Any, dict], None] | None = None`, and inside the send branch, after `log_send(...)` in the same `with tx_factory() as tx:` block, call `if post_send: post_send(tx, row)`. Register `e3` in `_CAMPAIGNS` and pass `post_send=getattr(mod, 'post_send', None)` in the admin send endpoint. Preview rows for E3 use `build_for(dict(person_id=0, email=to, name='Preview', newcomers=[dict(first_name='Rivka', country='GB')], total_new=1))`; give `_CAMPAIGNS` modules an optional `preview_row(to)` function and use it in the preview endpoint when present.

- [ ] **Step 4: Run tests and gates**

Scoped then full suite. Preview to `admin@techbaseltd.com`, check both mail clients.

- [ ] **Step 5: Commit (hold)**

```bash
git add emails/reinvite.py emails/send_reinvite.py service/campaigns/runner.py service/api/admin/growth_routes.py tests/test_reinvite.py
git commit -m "feat(emails): 30-day re-invite with newcomer names and resend guard"
```

---

### Task 10: E2 weekly community email (replaces the digest)

**Files:**
- Create: `emails/community_weekly.py`, `emails/send_community_weekly.py`
- Modify: `emails/send_digest.py` (top docstring: "Superseded by send_community_weekly on 2026-09-13; kept for one release, do not schedule")
- Modify: `service/unsubscribe/__init__.py` (`_SCOPES` gains `"community"`; `_Q_UNSUB['community']` stamps a new column, so also add to migration 0040 below)
- Create: `migrations/0040_community_unsubscribe.sql` (`ALTER TABLE person ADD COLUMN IF NOT EXISTS community_unsubscribed_at timestamptz;`)
- Modify: `service/api/admin/growth_routes.py` (`_CAMPAIGNS['e2'] = e2`)
- Test: `tests/test_community_weekly.py`

**Interfaces:**
- Consumes: `growth_stats` (Task 6), runner (Task 8), `make_campaign_link` (Task 4).
- Produces: `community_weekly_html(new_members: list[dict], total_members: int, spotlight: dict | None, cta_url: str, unsubscribe_url: str) -> str` where `spotlight` is `{first_name, age, country, image_url, post_url}` or None (Phase B supplies it; Phase A always passes None); `recipients()` = activated, not deleted, `community_unsubscribed_at IS NULL`; `build_for(row)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_community_weekly.py
from emails.community_weekly import community_weekly_html, SUBJECT

def test_weekly_without_spotlight_lists_newcomers():
    html = community_weekly_html([dict(first_name='Rivka', country='GB'), dict(first_name='Dan', country='US')], 27, None,
                                 'https://ahavah.app/s/k', 'https://ahavah.app/u/x')
    assert 'title-community.png' in html and 'Rivka' in html and 'Dan' in html and '27 members' in html
    assert 'Member of the week' not in html
    assert '\u2014' not in html and '\u2014' not in SUBJECT

def test_weekly_with_spotlight_block():
    html = community_weekly_html([], 27, dict(first_name='Sarah', age=29, country='US', image_url='https://x/y.png', post_url='https://fb/p'), 'https://a', 'https://u')
    assert 'Member of the week' in html and 'Sarah, 29' in html and 'https://x/y.png' in html
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```python
# emails/community_weekly.py
"""E2 weekly community email (spec 3.5). Replaces emails/digest.py.
Copy rules: NO em dashes. Sentence case."""
from __future__ import annotations

from typing import Optional
from service.config import EMAIL_DOMAIN
from emails.base import render, button, chip, title_image, callout, INK_SOFT, MUTED, SANS

FROM_ADDR = f"support@{EMAIL_DOMAIN}"
SUBJECT = "This week on Ahavah"

def _spotlight_block(sp: dict) -> str:
    return f"""
<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};font-weight:700;">Member of the week</p>
<a href="{sp['post_url']}" style="text-decoration:none;">
  <img src="{sp['image_url']}" alt="{sp['first_name']}, {sp['age']}, {sp['country']}" width="520" style="display:block;width:100%;max-width:520px;border-radius:16px;margin:0 0 10px;" />
</a>
<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{sp['first_name']}, {sp['age']} <span style="color:{MUTED};">in {sp['country']}</span></p>
"""

def _newcomers_block(rows: list[dict]) -> str:
    if not rows:
        return f'<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">No new members this week. Know someone who belongs here? Your invite link is in your profile.</p>'
    items = "".join(f'<li style="margin:0 0 6px;">{r["first_name"]}' + (f' <span style="color:{MUTED};">in {r["country"]}</span>' if r.get('country') else '') + '</li>' for r in rows)
    return f'<p class="e-text" style="margin:0 0 8px;font-family:{SANS};font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:{MUTED};font-weight:700;">New this week</p><ul style="margin:0 0 20px;padding-left:20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">{items}</ul>'

def community_weekly_html(new_members: list[dict], total_members: int, spotlight: Optional[dict], cta_url: str, unsubscribe_url: str) -> str:
    body = f"""
{chip("Community")}

{title_image("title-community.png", "title-community-wht.png", "This week on Ahavah.", 460)}

{_spotlight_block(spotlight) if spotlight else ''}

{_newcomers_block(new_members)}

<p class="e-text" style="margin:0 0 20px;font-family:{SANS};font-size:17px;line-height:1.55;color:{INK_SOFT};">
  The community is now <strong>{total_members} members</strong> across borders.
</p>

{button("Open Discover", cta_url)}

<div style="height:20px;line-height:20px;">&nbsp;</div>

{callout("Want to be featured? Turn on Spotlight under Settings, Privacy.")}
"""
    footer = f"""
Ahavah &middot; Torah-observant matchmaking for the diaspora.<br/>
You're receiving the weekly community email as a member of Ahavah.
<div style="margin-top:14px;">
  <a href="{unsubscribe_url}" style="color:{MUTED};font-weight:600;text-decoration:underline;">Stop the weekly email</a>
  &nbsp;&nbsp;&middot;&nbsp;&nbsp;
  <a href="https://ahavah.app/faq" style="color:{MUTED};font-weight:600;text-decoration:underline;">Help</a>
</div>
"""
    return render(title=SUBJECT, preheader="New members, community size, and this week's spotlight.", body_html=body, footer_html=footer)
```

```python
# emails/send_community_weekly.py
"""python -m emails.send_community_weekly [--send] [--campaign-id ID]"""
from __future__ import annotations

import argparse, uuid
from database import api_tx
from emails.community_weekly import community_weekly_html, SUBJECT, FROM_ADDR
from service.campaigns import make_campaign_link
from service.campaigns.runner import run_campaign
from service.config import WEB_BASE_URL
from service.growth.queries import _excluded
from service.unsubscribe import make_url as _unsub_url

_Q_RECIPIENTS = """
    SELECT id AS person_id, email, name FROM person
     WHERE activated AND deletion_requested_at IS NULL AND community_unsubscribed_at IS NULL
       AND lower(email) <> ALL(%(ex)s) ORDER BY id
"""
_Q_NEW = """
    SELECT split_part(name, ' ', 1) AS first_name, country FROM person
     WHERE activated AND sign_up_time > NOW() - interval '7 days' AND lower(email) <> ALL(%(ex)s)
     ORDER BY sign_up_time DESC
"""
_Q_TOTAL = "SELECT count(*) AS n FROM person WHERE activated AND lower(email) <> ALL(%(ex)s)"

def _week_context() -> dict:
    with api_tx('read committed') as tx:
        return dict(new_members=[dict(r) for r in tx.execute(_Q_NEW, dict(ex=_excluded())).fetchall()],
                    total=int(tx.execute(_Q_TOTAL, dict(ex=_excluded())).fetchone()['n']),
                    spotlight=None)   # Phase B fills this from the published queue

_CTX: dict = {}

def recipients() -> list[dict]:
    _CTX.update(_week_context())
    with api_tx('read committed') as tx:
        return [dict(r) for r in tx.execute(_Q_RECIPIENTS, dict(ex=_excluded())).fetchall()]

def build_for(row: dict) -> tuple[str, str]:
    if not _CTX:
        _CTX.update(_week_context())
    with api_tx() as tx:
        cta = make_campaign_link(tx, 'e2', f"{WEB_BASE_URL}/discover", row['person_id'] or None)
    return SUBJECT, community_weekly_html(_CTX['new_members'], _CTX['total'], _CTX['spotlight'], cta,
                                          _unsub_url('community', row['email'], WEB_BASE_URL))

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--send', action='store_true'); ap.add_argument('--campaign-id', default=None)
    a = ap.parse_args()
    cid = a.campaign_id or f"e2-{uuid.uuid4().hex[:8]}"
    print(run_campaign(api_tx, 'e2', cid, recipients(), build_for, send=a.send, from_addr=FROM_ADDR,
                       list_unsubscribe=lambda e: f"<mailto:support@ahavah.app?subject=Unsubscribe>, <{_unsub_url('community', e, WEB_BASE_URL)}>"))

if __name__ == '__main__':
    main()
```

In `service/unsubscribe/__init__.py`, add `"community"` to `_SCOPES` and a `_Q_UNSUB['community']` entry mirroring `notifications` but setting `community_unsubscribed_at = NOW()`. Extend `tests/test_unsubscribe*.py` (whichever exists) with one case for the new scope, and audit that `GET /u/<token>` still renders a page and only `POST` stamps; if GET stamps today, fix it here (spec section 2) and add the test that GET leaves the column NULL.

- [ ] **Step 4: Run tests and gates**

Apply migration 0040 in the test stack, run scoped then full suite. Preview to the admin address.

- [ ] **Step 5: Commit (hold)**

```bash
git add migrations/0040_community_unsubscribe.sql emails/community_weekly.py emails/send_community_weekly.py emails/send_digest.py service/unsubscribe/__init__.py service/api/admin/growth_routes.py tests/test_community_weekly.py tests/test_unsubscribe*.py
git commit -m "feat(emails): weekly community email with its own unsubscribe category"
```

---

### Task 11: Growth tab, stats and emails panels (admin.ahavah.app)

**Files:**
- Modify: `d:/Antigravity/ahavah-admin/src/lib/tabs.ts` (add `growth`)
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/tab-growth.tsx`
- Create: `d:/Antigravity/ahavah-admin/src/components/admin/growth-stats-panel.tsx`, `growth-emails-panel.tsx`
- Create: `d:/Antigravity/ahavah-admin/src/lib/growth-api.ts`
- Test: `d:/Antigravity/ahavah-admin/tests/growth-api.test.mjs`
- SOT: `d:/Antigravity/ahavah-admin/Claude Design/Growth Tab.html` (Task 2)

**Interfaces:**
- Consumes: `GET /admin/growth/stats`, `GET /admin/growth/emails`, `POST /admin/growth/emails/<campaign>/preview`, `POST /admin/growth/emails/<campaign>/send` (Tasks 6, 8, 9, 10). Auth and fetch go through the app's existing admin API client (read `src/lib/` for its name and error shape before writing `growth-api.ts`).
- Produces: `fetchGrowthStats()`, `fetchGrowthEmails()`, `previewEmail(campaign, to)`, `sendEmail(campaign, { campaignId, dryRun })` with `campaignId` minted once per confirmation dialog via `crypto.randomUUID()` and reused on retry.

- [ ] **Step 1: Pull the SOT and transcribe**

Run `/sot-sync "d:/Antigravity/ahavah-admin/Claude Design" growth`. Reuse the admin kit primitives (`design-primitives.tsx`, `kpi-card.tsx`, `AdminCard`), drop mockup chrome. Register the tab in `tabs.ts` following the seven existing entries.

- [ ] **Step 2: Write the failing API-client test**

```js
// tests/growth-api.test.mjs  (node:test, same style as the two existing admin tests)
import test from "node:test";
import assert from "node:assert/strict";
import { sendEmail } from "../src/lib/growth-api.ts";

test("sendEmail reuses one campaignId across a retry", async () => {
  const seen = [];
  globalThis.fetch = async (url, init) => { seen.push(JSON.parse(init.body).campaign_id); return new Response(JSON.stringify({ sent: 1 }), { status: 200 }); };
  const id = crypto.randomUUID();
  await sendEmail("e1", { campaignId: id, dryRun: true });
  await sendEmail("e1", { campaignId: id, dryRun: false });
  assert.deepEqual(seen, [id, id]);
});
```

If the admin app's tests import TypeScript through a loader, mirror the invocation the existing tests use (`node --test --import tsx` or the loader in `package.json`).

- [ ] **Step 3: Implement the client and panels**

```ts
// src/lib/growth-api.ts
import { adminFetch } from "./api-client"; // use the app's existing helper name

export type GrowthStats = {
  members_by_gender: Array<{ gender: string; members: number; new_7d: number; new_30d: number; acted_14d: number; stale_30d: number; never_acted: number; with_photo: number; premium: number; opted_in: number }>;
  matches: number; matches_30d: number; likes_total: number; likes_7d: number; msgs_7d: number; msgs_30d: number; opted_in: number;
};
export type GrowthEmail = { campaign: "e1" | "e2" | "e3"; recipients: number; last_sent_at: string | null; last_campaign_id: string | null };

export const fetchGrowthStats = () => adminFetch<GrowthStats>("/admin/growth/stats");
export const fetchGrowthEmails = () => adminFetch<{ campaigns: GrowthEmail[] }>("/admin/growth/emails");
export const previewEmail = (campaign: string, to: string) =>
  adminFetch<{ ok: true }>(`/admin/growth/emails/${campaign}/preview`, { method: "POST", body: JSON.stringify({ to }) });
export const sendEmail = (campaign: string, opts: { campaignId: string; dryRun: boolean }) =>
  adminFetch<{ sent: number; skipped_cap: number; skipped_suppressed: number; dry_run: boolean; campaign_id: string }>(
    `/admin/growth/emails/${campaign}/send`, { method: "POST", body: JSON.stringify({ campaign_id: opts.campaignId, dry_run: opts.dryRun }) });
```

Panels: the stats panel renders the SOT's KPI grid from `GrowthStats`; the emails panel renders one row per campaign with recipient count, last sent, a preview button (prompts for an address, defaults to the signed-in admin's email), a dry-run button, and a send button behind a confirmation dialog that shows the recipient count and the minted campaign id. Destructive controls are hidden at mobile widths, matching the other tabs.

- [ ] **Step 4: Gates and render verification**

`npx tsc --noEmit`, `node --test tests/`, production build. Playwright against the local build at 1440 and 390 with fixture responses for the four endpoints; compare to the SOT frames. One read-only real-session check on admin.ahavah.app after deploy.

- [ ] **Step 5: Commit (hold)**

```bash
git add src/lib/tabs.ts src/lib/growth-api.ts src/components/admin/tab-growth.tsx src/components/admin/growth-stats-panel.tsx src/components/admin/growth-emails-panel.tsx tests/growth-api.test.mjs
git commit -m "feat(admin): Growth tab with member stats and campaign email senders"
```

---

### Task 12: Terms and privacy sections for Spotlight

**Files:**
- Modify: `d:/Antigravity/ahavah-web/src/app/legal/privacy/page.tsx` (the sections array, after the "We don't sell your data" item)
- Modify: `d:/Antigravity/ahavah-web/src/app/legal/terms/page.tsx` (a "Spotlight" clause in the content licence section)
- Test: `d:/Antigravity/ahavah-web/tests/app/legal-spotlight.test.tsx`

**Interfaces:** none.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/app/legal-spotlight.test.tsx
import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import PrivacyPage from "@/app/legal/privacy/page";
import TermsPage from "@/app/legal/terms/page";

it("privacy page describes Spotlight and its opt-in", () => {
  render(<PrivacyPage />);
  expect(screen.getByText(/Spotlight/)).toBeTruthy();
  expect(screen.getByText(/only if you opt in/i)).toBeTruthy();
});
it("terms grant a limited licence only for members who opt in", () => {
  render(<TermsPage />);
  expect(screen.getByText(/Spotlight/)).toBeTruthy();
  expect(screen.getByText(/revoke/i)).toBeTruthy();
});
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, no Spotlight text.

- [ ] **Step 3: Add the copy**

Privacy section, exact copy:

```
title: "Spotlight"
body: "Spotlight features members on the Ahavah Facebook page, Instagram and the weekly community email, only if you opt in. If you do, we share your first name, age, country and one photo you choose, and you approve each card before it is posted. You can turn Spotlight off any time in Settings, Privacy; we then remove the card and delete posts we control. Posts on Instagram cannot be removed by us automatically and are removed by hand."
```

Terms clause, exact copy:

```
title: "Spotlight"
body: "If you opt in to Spotlight you grant Ahavah a limited, non-exclusive, revocable licence to publish the first name, age, country and the photo you approve for each Spotlight card on the Ahavah Facebook page, Instagram and member emails. You can revoke it at any time by turning Spotlight off in Settings, Privacy. Revocation stops future use; we remove existing posts we control within seven days."
```

- [ ] **Step 4: Run tests and gates**

`pnpm test -- tests/app/legal-spotlight.test.tsx && npx tsc --noEmit && npx eslint src/app/legal --max-warnings=0`. Render-verify both pages at 390.

- [ ] **Step 5: Commit (hold)**

```bash
git add src/app/legal/privacy/page.tsx src/app/legal/terms/page.tsx tests/app/legal-spotlight.test.tsx
git commit -m "docs(legal): Spotlight sections in privacy and terms"
```

---

### Task 13: Deploy Phase A and send E1 on owner go

**Files:** none new. Uses everything above.

- [ ] **Step 1: Full gates on all three repos**

API full suite green; web `pnpm test`, `tsc`, eslint on changed files, `next build`; admin `tsc`, `node --test`, build.

- [ ] **Step 2: Owner go, then push in this order**

1. `ahavah-api` (migrations 0039 and 0040 apply through the ledger runner; watch the Actions run and confirm `ahavah_schema_migration` shows both files).
2. `ahavah-web` (privacy switch, confirm page, `/s/` route, legal pages).
3. `ahavah-admin` (Growth tab).

Post-deploy checks (read-only): `curl -s -o /dev/null -w "%{http_code}" https://api.ahavah.app/spotlight/confirm/not.a.token` returns 400; `GET /s/doesnotexist` returns 404 on both hosts; the Growth tab loads stats on admin.ahavah.app for an admin session.

- [ ] **Step 3: Announcement**

From the Growth tab: preview E1 to `admin@techbaseltd.com`, inspect, dry run (expect recipients equal to activated members minus exclusions), then send with the owner present. Verify `email_send_log` rows equal the dry-run count and that one real opt-in through the emailed link flips `spotlight_opt_in` for that member.

- [ ] **Step 4: Record**

Append the deploy results, counts and the campaign id to `docs/superpowers/plans/2026-09-13-spotlight-preflight.md` and update the memory file `ahavah-next-action-card` with a pointer to this plan.

---

## Phase B (separate plan, after Task 2's card template lands)

Queue table and claim function (migration 0041), admin cron worker and Graph client port with the President tests, `next/og` card rendering and Spaces upload, member card approval (E4) and card-live (E5) emails, member of the week picker, kill switch and purge, removal tasks, click and sign-up attribution on posts, and the weekly email's spotlight block. It consumes `set_spotlight_opt_in` (Task 7) for opt-out cancellation, `growth_stats` (Task 6) and the runner (Task 8).

## Self-review record

- Spec coverage: 3.1 consent (Task 7), 3.2 eligibility (Phase B, consumes Task 7 columns), 3.3 kinds (Phase B), 3.4 growth loop and links (Task 4 and 5 for links; sharing email in Phase B), 3.5 E1 to E3 with cap, idempotency, category unsubscribe (Tasks 8 to 10), E4 and E5 (Phase B), 3.6 and 3.7 (Phase B), 3.8 stats and emails panels (Task 11), queue and member of the week panels (Phase B), section 2 GET safety (Tasks 7 and 10), terms and privacy (Task 12), pre-flight (Task 1), rollout steps 1 to 3 (Task 13).
- Known deviations: the re-invite applies gender and age preferences but not country or intent, which are client-side filters with no server persistence; recorded in Task 6.
- Type consistency: `run_campaign` signature is extended in Task 9 (`post_send`) and used with keyword arguments everywhere; `recipients()` and `build_for(row)` are the module contract every campaign module honours; `_CAMPAIGNS` keys are `e1`, `e2`, `e3` in both API and admin client.
