Linear: TEC-942

# Community Spotlight, Wave 3b (attribution and hardening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close F10 by rebuilding sign-up attribution on visitor-bound, single-use click receipts, and close the hardening items that can still take production down or hide a failure from an operator.

**Architecture:** Attribution stops travelling on the shared campaign key. A click mints its own opaque receipt row, the web app carries that receipt instead of the key, and the sign-up path credits a receipt only by consuming that exact row atomically. The hardening half removes the remaining ways a blank environment value can crash the cron container at import, makes the deploy fail when the cron container is not alive, and gives the operator the three lists that today exist only as counts.

**Tech Stack:** Flask + psycopg + pydantic (`ahavah-api`), pytest in the disposable Docker stack; Next.js 16 + React 19 (`ahavah-web`), vitest; GitHub Actions; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` sections 3.4 (growth loop and measurement) and 5 (error handling). Source findings: F10 in `C:/Users/Ehud/Documents/2026-09-14-community-spotlight-adversarial-review-and-remediation.md` and the triage at `docs/superpowers/plans/2026-09-14-spotlight-adversarial-remediation-triage.md`.

## Why now

Waves 1 to 3 are deployed and everything member-facing is dormant. Two things in this plan are not cosmetic:

- **The Wave 2 incident can recur today.** Six cron modules read their poll interval with `int(os.environ.get(NAME, default))`, which only survives a *missing* variable. All six are piped through `docker-compose.production.yml` as a bare `${VAR}` with no `:-default`, so a blank line in `.env.production` reaches `int('')` and crashes the whole cron container at import, exactly as happened on 2026-09-14. The deploy then reports green, because the workflow only probes the API's health endpoint and never looks at the cron container.
- **Attribution currently credits sign-ups that no click earned.** `attribute_signup` stamps a person as soon as the supplied ref is a known campaign key, before it looks for a click at all, then attaches whichever unmatched click for that key is newest. Numbers coming out of this must not be used to justify spending.

## Scope, stated against the parent work

Included: F10 in full (receipt minting, receipt-only crediting, single use, per-platform dimensions, documented touch policy), and hardening items 1, 3, 4, 5, 7, 8 and 9 from the survey.

Deliberately excluded, with the reason:

1. **Presign cold start.** Surveyed and found to be a non-issue: credentials are static strings and `generate_presigned_url` is a local HMAC with no network call. Nothing to harden. **Owner decision.**
2. **Compose and template coverage beyond the six unsafe vars.** Every variable already appears in the template; the only gap is the missing compose defaults, which task 6 closes for the vars that can actually crash a container. The dead `DUO_CRON_INSERT_LAST_POLL_SECONDS` wiring is removed as clutter.
3. **Bot detection quality.** `ua_class` stays a user-agent substring test. Hardening it properly is a separate problem and F10's acceptance criteria do not require it; what F10 requires is that a bot click cannot mint a usable receipt, which task 2 delivers. **Owner decision.**
4. **The staging acceptance matrix** stays Wave 4 and stays the gate for the first live post.

## Global Constraints

- Pushing `ahavah/main` or web `master` deploys production. Work on local branches `spotlight-wave-3b`; hold every push.
- Never nest `api_tx` inside an open `api_tx`. Fixtures before transactions. Storage and SMTP never inside a transaction. No literal `%` inside SQL passed to psycopg.
- No em dashes (U+2014) on added lines; sentence case; no attribution trailers in commits.
- Migration numbering: next free is `0048`; applied files are immutable.
- Backend tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 608). Web: `pnpm test` (baseline 574), `pnpm exec tsc --noEmit`, eslint on touched files.
- Backward compatibility is required on the API side. The web app and the API deploy separately, so for one deploy window an old web build will still send a campaign key where the new API expects a receipt. Task 3 must accept both and must say in the code why.

---

### Task 1: Migration 0048, click receipts

**Files:**
- Create: `migrations/0048_click_receipts.sql`
- Test: `tests/test_migration_0048.py`

**Interfaces:**
- Produces on `campaign_click`: `receipt text UNIQUE`, `platform text`, `consumed_at timestamptz`. `receipt` is nullable so existing rows stay valid; new rows always carry one.
- Produces index `campaign_click_receipt_idx ON campaign_click (receipt) WHERE receipt IS NOT NULL`.
- Existing rows are left with a null receipt on purpose: they predate the scheme and must never be creditable under it.

- [ ] **Step 1: Failing test**

```python
# tests/test_migration_0048.py
from database import api_tx

def test_receipt_columns_exist_and_are_unique():
    with api_tx() as tx:
        cols = {r['column_name'] for r in tx.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'campaign_click'
        """).fetchall()}
    assert {'receipt', 'platform', 'consumed_at'} <= cols

def test_receipt_is_unique_when_present(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        tx.execute("INSERT INTO campaign_click (link_key, receipt) VALUES (%(k)s, 'r1')", dict(k=key))
    with api_tx() as tx:
        try:
            tx.execute("INSERT INTO campaign_click (link_key, receipt) VALUES (%(k)s, 'r1')", dict(k=key))
            assert False, 'the second insert should have violated the unique index'
        except Exception:
            pass

def test_two_null_receipts_are_allowed(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        tx.execute("INSERT INTO campaign_click (link_key) VALUES (%(k)s)", dict(k=key))
        tx.execute("INSERT INTO campaign_click (link_key) VALUES (%(k)s)", dict(k=key))
```

Use the fixtures the neighbouring attribution tests already define; if `make_campaign_link` does not exist, build the link with `service.campaigns.make_campaign_link` before opening any transaction.

- [ ] **Step 2: Run, expect FAIL** (the columns do not exist).

- [ ] **Step 3: Implement**

```sql
-- 0048_click_receipts.sql
-- F10: a click now mints its own opaque receipt. The campaign key stays what
-- it always was, a shared identifier for the post, which is exactly why it
-- cannot be the thing that proves a particular visitor clicked.
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS receipt     text;
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS platform    text;
ALTER TABLE campaign_click ADD COLUMN IF NOT EXISTS consumed_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS campaign_click_receipt_idx
    ON campaign_click (receipt) WHERE receipt IS NOT NULL;
```

Rows written before this migration keep a null receipt and are therefore not creditable under the new scheme. That is deliberate, not an oversight: nothing proves who made them.

- [ ] **Step 4: Run, expect PASS. Apply twice on the warm stack to prove idempotence.**

- [ ] **Step 5: Commit** `feat(attribution): migration 0048, click receipts`.

---

### Task 2: Minting a receipt at click time

**Files:**
- Modify: `service/campaigns/__init__.py` (`record_click`), `service/api/campaign_link_routes.py` (`GET /s/<key>`)
- Test: `tests/test_spotlight_attribution.py` (new cases)

**Interfaces:**
- Consumes: `_ua_class(ua)` unchanged.
- Produces: `record_click(tx, key, user_agent, platform=None) -> tuple[Optional[str], Optional[str]]` returning `(target_url, receipt)`. A click classified `bot` records the row as it does today but returns `receipt=None`, so a crawler can never carry a usable receipt. `receipt = secrets.token_urlsafe(24)` (32 characters, inside the existing 32-character limit on the wire).
- `GET /s/<key>` accepts an optional `?p=facebook|instagram` and stores it in `platform`; anything else is stored as null. It sets no cookie itself, because the web app owns the cookie; it returns the receipt to the web forwarder in a `X-Spotlight-Receipt` response header on the redirect.

- [ ] **Step 1: Failing tests**

```python
def test_record_click_mints_a_receipt(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        target, receipt = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    assert target and receipt and len(receipt) <= 32
    with api_tx() as tx:
        row = tx.execute("SELECT receipt, ua_class FROM campaign_click WHERE receipt = %(r)s", dict(r=receipt)).fetchone()
    assert row['ua_class'] == 'mobile'

def test_a_bot_click_is_recorded_but_carries_no_receipt(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        target, receipt = record_click(tx, key, 'facebookexternalhit/1.1')
    assert target and receipt is None
    with api_tx() as tx:
        n = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s AND receipt IS NULL", dict(k=key)).fetchone()['n']
    assert n == 1

def test_two_clicks_mint_different_receipts(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, a = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        _, b = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    assert a != b

def test_platform_is_stored_when_named(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)', platform='instagram')
    with api_tx() as tx:
        assert tx.execute("SELECT platform FROM campaign_click WHERE receipt = %(r)s", dict(r=r)).fetchone()['platform'] == 'instagram'

def test_an_unknown_platform_is_stored_as_null(make_campaign_link):
    key = make_campaign_link()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)', platform='myspace')
    with api_tx() as tx:
        assert tx.execute("SELECT platform FROM campaign_click WHERE receipt = %(r)s", dict(r=r)).fetchone()['platform'] is None
```

- [ ] **Step 2: Run, expect FAIL** (`record_click` returns a single value).

- [ ] **Step 3: Implement.** `PLATFORMS = ('facebook', 'instagram')`; anything outside it becomes null rather than being trusted into the column. Update the one other caller of `record_click` if there is one (search first).

- [ ] **Step 4: Run scoped, then the full suite.**

- [ ] **Step 5: Commit** `feat(attribution): a click mints its own receipt`.

---

### Task 3: Crediting only a valid, unconsumed receipt

**Files:**
- Modify: `service/spotlight/attribution.py`
- Test: `tests/test_spotlight_attribution.py`

**Interfaces:**
- Produces `attribute_signup(tx, person_id, ref) -> bool` with these rules, in this order:
  1. `ref` absent or longer than 32 characters: return False.
  2. Try to consume it as a receipt: a single `UPDATE campaign_click SET signup_person_id = %(pid)s, consumed_at = NOW() WHERE receipt = %(ref)s AND signup_person_id IS NULL AND consumed_at IS NULL AND ua_class <> 'bot' AND clicked_at > NOW() - interval '7 days' RETURNING link_key`. That one statement is the whole gate: it proves the receipt exists, is unexpired, is not a bot's, and has never been consumed, and it consumes it atomically so two concurrent sign-ups cannot both claim it.
  3. If it consumed a row, stamp `person.spotlight_ref` with the returned `link_key` (not the receipt), first touch wins, and return True.
  4. If it consumed nothing, fall back to the legacy path **only** for a value that is a known `campaign_link.key`, and only until the compatibility window closes: stamp the person, credit nothing, and return True. This exists because the web app and the API deploy separately and an old web build still sends the key. The function must carry a comment saying so and naming task 7 as the removal.
- First touch is the documented policy: a person who already has a `spotlight_ref` is never re-stamped, and the receipt is not consumed in that case.

- [ ] **Step 1: Failing tests, one per acceptance criterion in the review**

```python
def test_no_click_gets_no_credit(make_campaign_link, make_person):
    key = make_campaign_link()   # minted, never clicked
    pid = make_person()
    with api_tx() as tx:
        # A receipt-shaped value that was never minted earns nothing.
        assert attribute_signup(tx, pid, 'not-a-real-receipt-value') is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=pid)).fetchone()['spotlight_ref'] is None

def test_an_expired_receipt_gets_no_credit(make_campaign_link, make_person):
    key = make_campaign_link(); pid = make_person()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        tx.execute("UPDATE campaign_click SET clicked_at = NOW() - interval '8 days' WHERE receipt = %(r)s", dict(r=r))
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r) is False

def test_a_receipt_is_single_use(make_campaign_link, make_person):
    key = make_campaign_link(); a, b = make_person(), make_person()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, a, r) is True
    with api_tx() as tx:
        assert attribute_signup(tx, b, r) is False
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=b)).fetchone()['spotlight_ref'] is None

def test_one_visitors_receipt_cannot_be_claimed_by_another(make_campaign_link, make_person):
    # Two visitors click the same link. Each receipt credits only its own click row.
    key = make_campaign_link(); a, b = make_person(), make_person()
    with api_tx() as tx:
        _, ra = record_click(tx, key, 'Mozilla/5.0 (iPhone)')
        _, rb = record_click(tx, key, 'Mozilla/5.0 (Android)')
    with api_tx() as tx:
        assert attribute_signup(tx, a, ra) is True
    with api_tx() as tx:
        assert attribute_signup(tx, b, rb) is True
    with api_tx() as tx:
        rows = tx.execute("SELECT receipt, signup_person_id FROM campaign_click WHERE link_key = %(k)s ORDER BY id", dict(k=key)).fetchall()
    assert {r['receipt']: r['signup_person_id'] for r in rows} == {ra: a, rb: b}

def test_a_bot_receipt_does_not_exist_to_be_claimed(make_campaign_link, make_person):
    key = make_campaign_link(); pid = make_person()
    with api_tx() as tx:
        _, r = record_click(tx, key, 'facebookexternalhit/1.1')
    assert r is None
    with api_tx() as tx:
        assert attribute_signup(tx, pid, 'anything') is False

def test_first_touch_wins_and_does_not_consume_the_second_receipt(make_campaign_link, make_person):
    k1, k2 = make_campaign_link(), make_campaign_link(); pid = make_person()
    with api_tx() as tx:
        _, r1 = record_click(tx, k1, 'Mozilla/5.0 (iPhone)')
        _, r2 = record_click(tx, k2, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r1) is True
    with api_tx() as tx:
        assert attribute_signup(tx, pid, r2) is False
        row = tx.execute("SELECT signup_person_id, consumed_at FROM campaign_click WHERE receipt = %(r)s", dict(r=r2)).fetchone()
    assert row['signup_person_id'] is None and row['consumed_at'] is None

def test_the_legacy_campaign_key_still_stamps_but_credits_nothing(make_campaign_link, make_person):
    # Compatibility window only: an old web build sends the shared key.
    key = make_campaign_link(); pid = make_person()
    with api_tx() as tx:
        record_click(tx, key, 'Mozilla/5.0 (iPhone)')
    with api_tx() as tx:
        assert attribute_signup(tx, pid, key) is True
        assert tx.execute("SELECT spotlight_ref FROM person WHERE id = %(p)s", dict(p=pid)).fetchone()['spotlight_ref'] == key
        credited = tx.execute("SELECT count(*) AS n FROM campaign_click WHERE link_key = %(k)s AND signup_person_id IS NOT NULL", dict(k=key)).fetchone()['n']
    assert credited == 0
```

- [ ] **Step 2: Run, expect FAIL** (today the first test passes for the wrong reason and the single-use test fails).

- [ ] **Step 3: Implement** exactly the ordered rules above. Delete the `ORDER BY clicked_at DESC LIMIT 1` sub-select entirely; nothing may pick a click by recency again.

- [ ] **Step 4: Full suite.**

- [ ] **Step 5: Commit** `fix(attribution): credit only a valid, unconsumed receipt`.

---

### Task 4: Per-platform stats

**Files:**
- Modify: `service/growth/queries.py` (`post_stats`), `service/api/admin/spotlight_routes.py` (`_Q_ROWS` clicks and signups sub-selects)
- Test: `tests/test_spotlight_attribution.py`

**Interfaces:**
- Produces `post_stats(tx, request_key) -> {'clicks': int, 'signups': int, 'by_platform': {'facebook': {'clicks': int, 'signups': int}, 'instagram': {...}, 'unknown': {...}}}`. The top-level totals keep their present meaning, so no existing caller breaks: `clicks` counts every non-bot click for the post and `signups` counts distinct credited people. `by_platform` splits the same rows on `campaign_click.platform`, with rows whose platform is null under `unknown`.
- The queue row's `clicks` and `signups` keep their current shape. This task adds the split, it does not reshape what the Growth tab already reads.

- [ ] Steps: failing test (two clicks on different platforms, one credited, assert the split and that the totals still reconcile to the sum of the parts), implement, full suite, commit `feat(growth): per-platform click and signup split`.

---

### Task 5: The web app carries the receipt

**Files:**
- Modify: `ahavah-web/src/app/s/[key]/route.ts`, `src/lib/spotlight-ref.ts`
- Test: `ahavah-web/tests/app/s-route.test.ts`, `tests/lib/spotlight-ref.test.ts`

**Interfaces:**
- The forwarder calls the API itself rather than redirecting the browser to it, so it can read the receipt: `fetch(apiOrigin + '/s/' + key + platformQuery, { redirect: 'manual' })`, take `X-Spotlight-Receipt` from the response, set the cookie to that value, and redirect the browser to the API's `Location`. On any failure, fall back to today's behaviour: redirect without a cookie. A click that cannot mint a receipt must still reach the destination.
- `?p=` is passed through when present and is one of `facebook` or `instagram`.
- The cookie keeps its name, 7-day lifetime, path, `sameSite` and `secure` flags. Only the value's meaning changes, from the shared post key to this one click's receipt.
- `KEY_PATTERN` in `spotlight-ref.ts` already accepts 1 to 32 characters of the url-safe alphabet, which a 32-character receipt satisfies. Confirm with a test rather than by eye.

- [ ] Steps: failing tests (receipt cookie set from the header; no header means no cookie but still a redirect; an upstream failure still redirects; a valid `?p=` is forwarded and an invalid one is dropped; `readSpotlightRef` accepts a 32-character receipt), implement, gates, commit `feat(spotlight): the click cookie carries a receipt, not the shared key`.

---

### Task 6: Cron blank-value safety and a deploy that checks the cron container

**Files:**
- Modify: the six modules that are piped through production compose and read an interval unsafely: `service/cron/profilereporter/__init__.py:16`, `notifications/__init__.py:27`, `autodeactivate2/__init__.py:15`, `photocleaner/__init__.py:17`, `audiocleaner/__init__.py:17`, `garbagerecords/__init__.py:11`; then the nine that are not currently piped through but read the same way: `fireholbuilder`, `pendingdeletion` (two values), `verificationjobrunner`, `entitlements`, `nsfwphotorunner`, `checkphotos`, `betareengagement` (two values)
- Modify: `docker-compose.production.yml` (a `:-default` on every cron interval variable it passes), remove the dead `DUO_CRON_INSERT_LAST_POLL_SECONDS` wiring from both compose files and the template
- Modify: `.github/workflows/deploy-ahavah.yml`
- Create: `tests/test_cronutil.py`

**Interfaces:**
- Every one of those reads becomes `cronutil.env_int(NAME, default)`, which already treats blank and missing alike. No interval's default value changes.
- The deploy workflow gains, after the API health check passes: an assertion that the `cron` container is running and has not restarted, and a scan of its recent log for a traceback. The step fails the deploy if the container is not up.

- [ ] **Step 1: Failing test for the utility itself** (`tests/test_cronutil.py`): `env_int` returns the default for a missing name, for an empty string, and for whitespace; returns the parsed value for a real number; and raises for a non-numeric non-blank value rather than silently defaulting. This is the regression the Wave 2 incident asked for and it has never had a test.

- [ ] **Step 2: Failing test for the modules.** A test that imports every package under `service/cron/` with every `DUO_CRON_*` environment variable set to the empty string, and asserts none of them raises. Discover the modules with `pkgutil.iter_modules` rather than listing them, so a cron added later is covered without anyone remembering to add it here.

- [ ] **Step 3: Run, expect FAIL** naming the first module that raises.

- [ ] **Step 4: Implement** the fifteen call sites and the compose defaults.

- [ ] **Step 5: Deploy workflow.** After the existing health loop:

```yaml
          echo "::group::cron container"
          cid=$(docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production ps -q cron)
          if [ -z "$cid" ]; then echo "  cron container is not present"; exit 1; fi
          state=$(docker inspect -f '{{.State.Status}}' "$cid")
          restarts=$(docker inspect -f '{{.RestartCount}}' "$cid")
          echo "  cron state=$state restarts=$restarts"
          if [ "$state" != "running" ]; then
            echo "  cron is not running"; docker logs --tail 60 "$cid" || true; exit 1
          fi
          if docker logs --tail 200 "$cid" 2>&1 | grep -qi traceback; then
            echo "  cron logged a traceback"; docker logs --tail 60 "$cid" || true; exit 1
          fi
          echo "::endgroup::"
```

Match the surrounding script's style and quoting; the block above is the intent, not a literal patch.

- [ ] **Step 6: Full suite, commit** `fix(cron): every interval tolerates a blank value and the deploy fails when cron is down`.

---

### Task 7: Operator surfaces for the three counts

**Files:**
- Modify: `service/api/admin/spotlight_routes.py` (`_Q_WELCOME_CANDIDATES`, the removals route), `service/spotlight/cleanup.py` (`abandoned_jobs`), `service/campaigns/outbox.py` (a cross-campaign unknown summary), `service/api/admin/growth_routes.py`
- Test: `tests/test_spotlight_routes.py`, `tests/test_growth_routes.py`, `tests/test_spotlight_cleanup.py`

**Interfaces:**
- `_Q_WELCOME_CANDIDATES` gains `AND q.status <> 'cancelled'` inside its `NOT EXISTS`, so a member whose welcome was cancelled is offered again, matching the guard the creation route already uses. Today the write path allows it and the read path hides it, which makes the existing fix unreachable from the admin.
- `abandoned_jobs(tx)` keeps returning the count and gains `abandoned_job_rows(tx, limit=50) -> list[dict]` with `id, kind, target, attempts, last_error, updated_at`. `GET /admin/growth/removals` returns them as `abandoned` alongside the existing `abandoned_cleanup` count, so an operator can see which object keys are stuck without opening psql.
- `outbox.unknown_summary(tx) -> list[dict]` returns `campaign, campaign_id, n` for every campaign with at least one `acceptance_unknown` row, newest first. A new `GET /admin/growth/emails/unknown` (admin only) returns it. This is the cross-campaign view that does not exist today: the present status route requires the operator to already know the campaign id they are looking for.
- `invites_pending` gains an age: `GET /admin/growth/candidates` returns `invites_pending_oldest_days` alongside the count, computed from the oldest row's `created_at`. No row is auto-cancelled; a backlog that is quietly ageing becomes visible instead of silently accumulating.

- [ ] Steps: failing tests per bullet, implement, full suite, commit `feat(growth): abandoned jobs, unknown mail and a stale invite backlog are visible to an operator`.

---

### Task 8: Documentation and the compatibility window

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (3.4), `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md` (section 14), memory
- Create: `docs/superpowers/plans/2026-09-15-spotlight-wave-3b-evidence.md`

- [ ] Amend spec 3.4: attribution is receipt-based, first touch, single use, seven days, bot clicks mint no receipt, and per-platform splits exist. State plainly that numbers produced before this wave are not trustworthy and must not justify spending.
- [ ] Handoff section 14: what shipped, the branches and heads, the deploy order (API first, then web, because the API must accept receipts before the web app starts sending them), and the compatibility window: the legacy campaign-key branch in `attribute_signup` may be deleted once both are deployed and the 7-day cookie lifetime has passed, which is the earliest date any browser can still be carrying an old key. Name that date.
- [ ] Evidence document and memory update.
- [ ] Commit `docs(spotlight): wave 3b record`.

---

### Task 9: Deploy

- [ ] Push order on owner go: API first (migration 0048 applies, `attribute_signup` accepts both shapes), then web. Reversing the order would have the web app minting receipts an older API cannot consume.
- [ ] Post-deploy: `/health` ok, all six containers up including cron with no traceback, and the new deploy-workflow cron assertion green on its first real run.
- [ ] Record the date on which the legacy key branch can be removed.

## Self-review record

- Spec coverage: 3.4 attribution (tasks 1 to 5), 3.4 measurement split (task 4), 5 error handling and operator visibility (tasks 6 and 7), documentation (task 8).
- Placeholder scan: every task names files and exact values; the SQL, the ordered rules in task 3 and the workflow block in task 6 are written out.
- Type consistency: `record_click` returns a tuple in task 2 and every caller is updated there; `attribute_signup` keeps its `(tx, person_id, ref) -> bool` signature so the finish-onboarding call site is untouched; `post_stats` adds a key and changes none, so the Growth tab keeps working without a change in the admin repo.
- Ordering risk: task 5 depends on task 2's header and task 3's acceptance of both shapes, so 5 must not land before 3. Task 6 and task 7 are independent of the attribution chain and can run in parallel with it on the API tree only if no two implementers stage at once.
