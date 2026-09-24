Linear: TEC-942

# Community Spotlight, Wave 3 (designed surfaces) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build every member-facing and operator-facing surface that Waves 1 and 2 left dormant, transcribed from the Claude Design export retrieved on 2026-09-15: the card renderer, the member card approval page, the confirmation page, the privacy switch row, and the Growth tab.

**Architecture:** The API already holds every state machine; Wave 3 adds three small read-side fields and one email row, then builds UI on top. The renderer is a `next/og` `ImageResponse` transcription of the card template inside `ahavah-admin`, called in-process by the existing daily tick and posting base64 PNGs to the existing attach route. The Growth tab is a new admin tab on the existing tab registry and design primitives, talking to the existing `/admin/growth/*` routes through the existing session-authenticated `/api` proxy. The two member pages and the switch row are `ahavah-web` client pages on the existing API client, following the claim page pattern for unauthenticated token pages.

**Tech Stack:** Flask + psycopg (`ahavah-api`, pytest in the disposable Docker stack); Next.js 16.2.6 + React 19.2.4 (`ahavah-admin`: `next/og` `ImageResponse`, `@tanstack/react-query`, `node:test` with the `vm` transpile harness; `ahavah-web`: vitest + testing-library); Google Fonts static TTFs (Ultra, Plus Jakarta Sans, Noto Serif Hebrew).

**Spec:** `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` sections 3.1, 3.3, 3.5 (E4, E5), 3.7, 3.8, 7 and 9. Design source of truth (SOT), pulled 2026-09-15 into `d:/Antigravity/ahavah-web/Claude Design/`: `Ahavah Spotlight Card Template.html`, `Ahavah Spotlight Member Surfaces.html`, `Ahavah Spotlight Growth Tab.html`, `Ahavah Spotlight Email Titles.html`. Brief: `ahavah-web/docs/design-briefs/2026-09-13-community-spotlight.md`. Triage sequencing: `docs/superpowers/plans/2026-09-14-spotlight-adversarial-remediation-triage.md` ("Wave 3 and 4").

## Scope against the parent plan and the triage (owner decisions, headline)

Included: renderer (Phase B Task 5), card approval page (Phase B Task 4b), confirm page and privacy switch (Phase A Task 7b), Growth tab all five sections (Phase A Task 11 and Phase B Task 10), the email title images already delivered by the design (six PNGs, manual download).

Deliberately left out of this wave, with the reason:

1. **F10 attribution rebuilt on visitor-bound click receipts** and **the remainder of F12** (idempotent-tick work beyond the weekly key, any auto mode). Both are sequenced after the designed surfaces in the triage; they need no design and follow as Wave 3b.
2. **The photo picker on the card approval page.** The brief dropped the approval page from the design round and said it reuses the confirm page pattern plus the card. The API accepts a different `photo_uuid` and answers `new_revision`, but a thumbnail picker has no design. Wave 3 ships approve or skip of the rendered card only, passing the card's own `photo_uuid`. A follow-up brief covers the picker. **Owner decision.**
3. **The staging acceptance-matrix run** stays Wave 4 and stays the gate for the first live post.
4. **Hardening minors** parked in the Wave 2 ledger (blank-tolerant parsing in the older crons, a deploy check that asserts the cron container is up, `invites_pending` age-out, presign cold start, `acceptance_unknown` alerting): a separate small pass, not this wave.

Deviations from the SOT, ruled here because the spec outranks a design drawn from the pre-Wave-1 brief:

5. **Controls panel.** The SOT shows Scheduler, Auto welcome and Auto roundup switches. Wave 1 removed those (spec 3.6). The panel uses the SOT's switch-row pattern for the three current controls, `invites_enabled`, `publication_enabled`, `external_access_enabled`, with the SOT's exact copy replaced by the spec's meaning, plus two read-only status chips, `approvals_enabled` and `roundup_tiles_enabled`. **Owner decision.**
6. **Queue rows.** The API returns one row per platform; the SOT shows one row per card with platform chips. The tab groups rows by `request_key`; when the two platform rows disagree on status the row shows one badge per platform ("Facebook published", "Instagram failed"). The row menu keeps the SOT's five actions mapped to the existing routes: Approve (`/approve`), Post now (`/reschedule` with `scheduled_for` = now), Reschedule (`/reschedule`), Cancel (`/cancel`), Copy caption and open post (clipboard + `post_url`).
7. **Card caption line.** The API holds one `caption` per card, the social post text with the `/s/<key>` link appended. The card's one-line caption is derived by the renderer: the first sentence of the caption with any `https://` link removed, clamped to two lines. The queue-time 120-character rejection named on the SOT spec board is not implemented; the clamp covers overflow. **Owner decision.**
8. **Hebrew display substitute.** Ultra carries Latin and Latin Extended only. Names render with `Ultra, "Noto Serif Hebrew"`, Noto Serif Hebrew at weight 900 being the closest slab-weight serif with a Hebrew block. A name that still has no glyph in any loaded font is a render failure (`render_failed` on the tick), never a card with tofu. **Owner decision.**
9. **Card approval page copy.** No dedicated SOT exists; the page reuses the confirm page shell and the copy in Task 5 is written to the brief's rules (sentence case, one paragraph, no em dashes). **Owner decision.**
10. **Preview button on the Emails panel.** The API preview route needs a `to` address; the panel sends the preview to the signed-in admin's own email from `/admin/whoami`.

## Global Constraints

- Pushing `ahavah/main`, web `master` or admin `master` deploys production. Work on local branches `spotlight-wave-3` in all three repos, forked from the deployed heads (api `ebc7d5e`, admin `1b9479d`, web `7728165`); hold every push for the owner's go.
- Never nest `api_tx` inside an open `api_tx` (the connection lock is not reentrant). Never run two API test processes at once. Fixtures before transactions. Storage and SMTP never inside a transaction. No literal `%` inside SQL passed to psycopg.
- No em dashes anywhere in touched files (sweep for the U+2014 byte before every commit); sentence case in user-facing strings; English only. No link changes member state on GET. Tokens in `Authorization: Bearer` or a dedicated header, never in URLs. Names and photos on social only for opted-in members with an approved card.
- The SOT files are law for layout, spacing, colour and copy ([[feedback_sot_html_is_law]]); deviations are only the ten numbered above. Reuse repo primitives, drop mockup chrome (phone frames, status bars, frame captions, the lavender "isnew" inset rule which the SOT itself marks as document-only).
- Every UI task ends with real rendered screenshots at the SOT widths (1440 and 390 for admin, 390 and 1440 for the member pages, 390 dark and light for the switch row) taken with headless Chrome under CDP mobile emulation, attached to the task report and compared by the reviewer against the SOT frame. DOM checks are not verification.
- Backend tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 604). Admin: `node --test tests/*.test.mjs` (baseline 59), `npx tsc --noEmit`, `npx next build`. Web: `pnpm test` (baseline 544), `pnpm exec tsc --noEmit`, `pnpm exec eslint . --max-warnings 0`.
- Admin tests run TypeScript through `ts.transpileModule` inside a `vm` sandbox with a stub `fetch` and an explicit `require` map (see `tests/tick.test.mjs`). A module under test must therefore avoid `next/*` imports except where the test injects them through the map.
- Migration numbering: next free is `0048`; this wave needs none.
- No `Co-Authored-By` or AI attribution trailers in commits.

---

## File structure

**ahavah-api (Task 1)**
- Modify `service/api/admin/spotlight_routes.py`: `_Q_ROWS` also selects `q.image_key, q.post_url, q.delivery_state`; `_queue_row` adds `preview_url`, `post_url`, `delivery_state`; `get_growth_spotlight_suggest` adds `suggested_caption` per item.
- Modify `service/api/admin/growth_routes.py`: `GET /admin/growth/emails` appends `e4` and `e5` rows (system sent).
- Tests: `tests/test_spotlight_routes.py` (new cases), `tests/test_growth_emails.py` (new case).

**ahavah-admin (Tasks 2, 6, 7)**
- Create `assets/fonts/Ultra-Regular.ttf`, `PlusJakartaSans-Medium.ttf`, `PlusJakartaSans-SemiBold.ttf`, `PlusJakartaSans-Bold.ttf`, `NotoSerifHebrew-Black.ttf`, `LICENSE-Ultra.txt`, `LICENSE-PlusJakartaSans.txt`, `LICENSE-NotoSerifHebrew.txt`; `assets/brand/logo-mark-lime.svg`.
- Replace `src/lib/spotlight-card.ts` with `src/lib/spotlight-card.tsx` (renderer) and create `src/lib/spotlight-card-layout.tsx` (pure element builder, no `next/*` import) and `src/lib/spotlight-card-text.ts` (caption line derivation, pure).
- Create `src/lib/growth-api.ts` (browser client for the Growth tab: types, hooks-free fetchers, pure helpers), `src/lib/growth-queries.ts`, `src/lib/growth-mutations.ts`.
- Create `src/components/ui/switch.tsx` (Radix switch styled to the SOT `.sw`).
- Create `src/components/admin/tab-growth.tsx`, `growth-stats.tsx`, `growth-queue.tsx`, `growth-removals.tsx`, `growth-motw.tsx`, `growth-emails.tsx`, `growth-controls.tsx`, `growth-dialogs.tsx`.
- Modify `src/lib/tabs.ts` (add `growth`), `src/components/admin/tab-router.tsx`, `src/lib/types.ts` (Growth types).
- Tests: `tests/spotlight-card.test.mjs`, `tests/spotlight-card-text.test.mjs`, `tests/growth-api.test.mjs`. Dev dependency `pngjs` for pixel assertions.

**ahavah-web (Tasks 3, 4, 5)**
- Modify `src/app/settings/privacy/page.tsx` (Spotlight section and row).
- Create `src/components/app/spotlight-shell.tsx` (standalone brand shell shared by the two token pages), `src/app/spotlight/confirm/[token]/page.tsx`, `src/app/spotlight/card/[token]/page.tsx`, `src/lib/spotlight-copy.ts` (every string for the three surfaces, verbatim from the SOT).
- Tests: `tests/lib/privacy-spotlight.test.tsx`, `tests/app/spotlight-confirm.test.tsx`, `tests/app/spotlight-card.test.tsx`.

**Docs (Task 8)**: spec amendments, handoff section 13, evidence document `docs/superpowers/plans/2026-09-15-spotlight-wave-3-evidence.md`, memory update.

---

### Task 1: API read-side fields for the Growth tab

**Files:**
- Modify: `service/api/admin/spotlight_routes.py` (`_Q_ROWS` around line 301, `_queue_row` at line 512, `get_growth_spotlight_suggest` at line 1477)
- Modify: `service/api/admin/growth_routes.py` (`get_admin_growth_emails` at line 35)
- Test: `tests/test_spotlight_routes.py`, `tests/test_growth_emails.py`

**Interfaces:**
- Consumes: `service.spotlight.storage.presign(key, seconds=900) -> str | None`; `_member_of_week_caption(first_name, age, country)` at line 1533; `email_send_log(campaign, campaign_id, person_id, sent_at)`.
- Produces: every `GET /admin/growth/queue` row gains `preview_url: str | None` (presigned when `image_key` is set and storage is configured, else `image_url`), `post_url: str | None`, `delivery_state: str`. `GET /admin/growth/spotlight/suggest` items gain `suggested_caption: str`. `GET /admin/growth/emails` returns five campaigns: `e1`, `e2`, `e3` as today plus `e4` and `e5` as `{campaign, recipients (count of email_send_log rows for that campaign), last_sent_at, last_campaign_id, system: true}`; the first three carry `system: false`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_spotlight_routes.py (append)
def test_queue_row_carries_preview_post_url_and_delivery_state(client, admin_headers, welcome_row, monkeypatch):
    import service.spotlight.storage as st
    monkeypatch.setattr(st, 'presign', lambda key, seconds=900: f'https://signed.test/{key}')
    with api_tx() as tx:
        tx.execute("UPDATE publishing_queue SET image_key = 'spotlight/k/1-abc-facebook.png', post_url = 'https://www.facebook.com/1', delivery_state = 'published' WHERE request_key = %(rk)s", dict(rk=welcome_row))
    rows = client.get('/admin/growth/queue', headers=admin_headers).get_json()
    row = next(r for r in rows if r['request_key'] == welcome_row)
    assert row['preview_url'] == 'https://signed.test/spotlight/k/1-abc-facebook.png'
    assert row['post_url'] == 'https://www.facebook.com/1'
    assert row['delivery_state'] == 'published'

def test_queue_row_preview_falls_back_to_image_url_without_key(client, admin_headers, welcome_row):
    rows = client.get('/admin/growth/queue', headers=admin_headers).get_json()
    row = next(r for r in rows if r['request_key'] == welcome_row)
    assert row['preview_url'] == row['image_url']

def test_suggest_carries_a_default_caption(client, admin_headers, eligible_member):
    items = client.get('/admin/growth/spotlight/suggest', headers=admin_headers).get_json()
    assert items and items[0]['suggested_caption'].startswith('Member of the week: ')
```

```python
# tests/test_growth_emails.py (append)
def test_emails_index_lists_system_sent_campaigns(client, admin_headers, person):
    with api_tx() as tx:
        tx.execute("INSERT INTO email_send_log (person_id, campaign, campaign_id, sent_at) VALUES (%(p)s, 'e4', 'e4-rk1', NOW())", dict(p=person))
    out = client.get('/admin/growth/emails', headers=admin_headers).get_json()['campaigns']
    keys = [c['campaign'] for c in out]
    assert keys == ['e1', 'e2', 'e3', 'e4', 'e5']
    e4 = next(c for c in out if c['campaign'] == 'e4')
    assert e4['system'] is True and e4['recipients'] == 1 and e4['last_campaign_id'] == 'e4-rk1'
    assert next(c for c in out if c['campaign'] == 'e1')['system'] is False
```

Use the fixtures the existing files already define for an admin session, a welcome row and an eligible member (`welcome_row`, `eligible_member`, `person` are the names used in `tests/test_spotlight_routes.py` and `tests/test_growth_emails.py`; if a fixture is missing, build it with `make_person` and the welcome route as the neighbouring tests do, before any transaction).

- [ ] **Step 2: Run, expect FAIL** (`KeyError: 'preview_url'`, `KeyError: 'suggested_caption'`, campaign list of three).

- [ ] **Step 3: Implement**

`_Q_ROWS`: add `q.image_key, q.post_url, q.delivery_state,` after `q.image_url,`.

`_queue_row`:
```python
    key = r['image_key']
    preview = None
    if key:
        from service.spotlight import storage
        preview = storage.presign(key)
    row = dict(
        ...,
        image_url=r['image_url'],
        preview_url=preview or r['image_url'],
        post_url=r['post_url'],
        delivery_state=r['delivery_state'],
        ...
    )
```
`presign` runs outside any transaction already (`_queue_row` is called after the read block closes; keep it that way, and check the call site: if `_queue_row` is invoked inside `with api_tx(...)`, move the presign loop after the block).

`get_growth_spotlight_suggest`: for each item add `suggested_caption=_member_of_week_caption(first_name, age, country)`.

`get_admin_growth_emails`: after the loop over `_CAMPAIGNS` append, inside the same read transaction as `_Q_LAST_SENT` (extend that query with a `count(*) AS n` column):
```python
    for key in ('e4', 'e5'):
        last = last_sent.get(key)
        out.append(dict(campaign=key, recipients=int(last['n']) if last else 0,
                        last_sent_at=last['at'].isoformat() if last and last['at'] else None,
                        last_campaign_id=last['cid'] if last else None, system=True))
```
and add `system=False` to the three existing rows.

- [ ] **Step 4: Scoped tests then the full suite**, expect 604 + 4.

- [ ] **Step 5: Commit** `feat(growth): queue rows carry a presigned preview, post url and delivery state; suggest carries a default caption; emails index lists system-sent campaigns`.

---

### Task 2: Card renderer (admin, `next/og`)

**Files:**
- Create: `assets/fonts/*` (see file structure), `assets/brand/logo-mark-lime.svg` (copy of `ahavah-web/Claude Design/assets/brand/logo-mark-lime.svg`), `src/lib/spotlight-card-text.ts`, `src/lib/spotlight-card-layout.tsx`, `src/lib/spotlight-card.tsx`
- Delete: `src/lib/spotlight-card.ts` (the stub)
- Modify: `package.json` (devDependency `pngjs`), `tsconfig.json` only if `jsx` is not already `react-jsx`
- Test: `tests/spotlight-card-text.test.mjs`, `tests/spotlight-card.test.mjs`

**Interfaces:**
- Consumes: `CardInput` exactly as `src/lib/tick.ts` `renderInputFor` builds it today (`variant: 'photo' | 'member_of_week'` with `firstName, age?, country?, caption, photoUrl?`; `variant: 'roundup'` with `tiles: Array<{first_name, photo_url}>, caption`; `variant: 'roundup_fallback'` with `count, countries, caption`). The tick's import path stays `./spotlight-card`, so the `.tsx` module must export the same `renderCard` and `CardInput` names.
- Produces: `renderCard(input: CardInput): Promise<Buffer>` resolving to a 1080 by 1080 PNG; `captionLine(caption: string): string`; `cardElement(input: CardInput, assets: { logo: string; photos: Map<string, string> }): ReactElement` (pure; `assets.logo` and every photo are data URIs).
- Fonts: loaded once per process with `readFile(join(process.cwd(), 'assets/fonts', name))`; names registered with satori as `Ultra` (400), `Plus Jakarta Sans` (500, 600, 700), `Noto Serif Hebrew` (900).
- Photos: fetched only from hosts in `AHAVAH_PHOTO_HOSTS` (comma list, default `user-images.ahavah.app`), 10 s timeout, at most 6 MB, converted to a data URI; any other host throws `Error('photo_host_refused')`. A `photo`/`member_of_week` input without `photoUrl` throws `Error('photo_missing')` (a card with a member's name and no photo must never be produced; the tick counts it as `render_failed`).

- [ ] **Step 1: Fonts and assets**

Download static TTFs (not variable fonts; satori ignores the weight axis). Use the Google Fonts CSS endpoint with a legacy user agent so it answers TTF URLs, then fetch each file:

```bash
cd d:/Antigravity/ahavah-admin && mkdir -p assets/fonts assets/brand
unzip -o "d:/Antigravity/ahavah-web/Claude Design/Ultra.zip" -d /tmp/ultra && cp /tmp/ultra/Ultra-Regular.ttf assets/fonts/ && cp /tmp/ultra/LICENSE.txt assets/fonts/LICENSE-Ultra.txt
UA="Mozilla/5.0 (Windows NT 6.1; rv:40.0) Gecko/20100101 Firefox/40.0"
curl -s -A "$UA" "https://fonts.googleapis.com/css?family=Plus+Jakarta+Sans:500,600,700|Noto+Serif+Hebrew:900"
# copy each url(...) the CSS names into assets/fonts with the file names in the file structure
cp "d:/Antigravity/ahavah-web/Claude Design/assets/brand/logo-mark-lime.svg" assets/brand/
```
Record the exact URLs fetched in the task report. Add the OFL text for Plus Jakarta Sans and Noto Serif Hebrew from `https://github.com/google/fonts/tree/main/ofl/<family>/OFL.txt`. `npm i -D pngjs`.

- [ ] **Step 2: Failing tests**

```js
// tests/spotlight-card-text.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const src = ts.transpileModule(readFileSync(new URL('../src/lib/spotlight-card-text.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const m = { exports: {} }; vm.runInNewContext(src, { module: m, exports: m.exports });
const { captionLine } = m.exports;

test('caption line is the first sentence without the campaign link', () => {
  assert.equal(captionLine('Welcome to Ahavah, Rivka. California, United States. https://ahavah.app/s/abc123'), 'Welcome to Ahavah, Rivka.');
});
test('a caption that is only a link gives an empty line', () => {
  assert.equal(captionLine('https://ahavah.app/s/abc123'), '');
});
test('a first sentence longer than 120 characters is cut at a word boundary with an ellipsis character', () => {
  const long = 'A'.repeat(50) + ' ' + 'B'.repeat(50) + ' ' + 'C'.repeat(50) + '.';
  const line = captionLine(long);
  assert.ok(line.length <= 121 && line.endsWith('\u2026'));
});
```

```js
// tests/spotlight-card.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { PNG } from 'pngjs';
const require = createRequire(import.meta.url);
const React = require('react');
const og = require('next/og');

const PHOTO = 'data:image/png;base64,' + PNG.sync.write(Object.assign(new PNG({ width: 4, height: 4 }), { data: Buffer.alloc(64, 0x80) })).toString('base64');

function load(rel) {
  const out = ts.transpileModule(readFileSync(new URL(rel, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const m = { exports: {} };
  vm.runInNewContext(out, {
    module: m, exports: m.exports, process, Buffer, console, URL, fetch: async () => { throw new Error('no network in tests'); },
    setTimeout, clearTimeout, AbortController,
    require: id => {
      if (id === 'react') return React;
      if (id === 'react/jsx-runtime') return require('react/jsx-runtime');
      if (id === 'next/og') return og;
      if (id === 'node:fs/promises' || id === 'fs/promises') return require('node:fs/promises');
      if (id === 'node:path' || id === 'path') return require('node:path');
      if (id.startsWith('./')) return load(`../src/lib/${id.slice(2)}.tsx`.replace('.ts.tsx', '.ts'));
      throw new Error(`Unexpected require: ${id}`);
    },
  });
  return m.exports;
}

function dims(buf) { const p = PNG.sync.read(buf); return [p.width, p.height, p]; }
function nonBackgroundPixels(png, x0, y0, x1, y1) {
  let n = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const i = (y * png.width + x) * 4;
    if (png.data[i] > 200 && png.data[i + 1] > 200 && png.data[i + 2] > 200) n++;
  }
  return n;
}

test('photo card renders at 1080 by 1080 with the name in white at the foot', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  const buf = await renderCard({ variant: 'photo', firstName: 'Rivka', age: 27, country: 'California, United States', caption: 'Welcome to Ahavah, Rivka. https://ahavah.app/s/x', photoUrl: PHOTO });
  const [w, h, png] = dims(buf);
  assert.equal(w, 1080); assert.equal(h, 1080);
  assert.ok(nonBackgroundPixels(png, 64, 700, 900, 1016) > 2000, 'name and country glyphs expected in the foot block');
});

test('member of the week adds the lime rule above the name', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  const [, , png] = dims(await renderCard({ variant: 'member_of_week', firstName: 'Aaliyah', age: 29, country: 'Trinidad and Tobago', caption: 'Member of the week: Aaliyah, 29.', photoUrl: PHOTO }));
  let lime = 0;
  for (let y = 600; y < 1016; y++) for (let x = 64; x < 184; x++) { const i = (y * 1080 + x) * 4; if (png.data[i] > 200 && png.data[i + 1] > 240 && png.data[i + 2] < 160) lime++; }
  assert.ok(lime >= 500, `expected a 120 by 5 lime rule, saw ${lime} lime pixels`);
});

test('roundup with four tiles and the fallback both render', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  const tiles = ['Rivka', 'Daniel', 'Aaliyah', 'Tehillah'].map(first_name => ({ first_name, photo_url: PHOTO }));
  assert.deepEqual(dims(await renderCard({ variant: 'roundup', tiles, caption: 'New this week on Ahavah.' })).slice(0, 2), [1080, 1080]);
  assert.deepEqual(dims(await renderCard({ variant: 'roundup_fallback', count: 7, countries: 4, caption: 'New this week on Ahavah.' })).slice(0, 2), [1080, 1080]);
});

test('a Hebrew and a Yoruba first name render without tofu', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  for (const firstName of ['\u05e8\u05d1\u05e7\u05d4', '\u1ecc\u006c\u00e1\u0077\u00f9\u006e\u006d\u00ed']) {
    const [, , png] = dims(await renderCard({ variant: 'photo', firstName, age: 26, country: 'Jerusalem, Israel', caption: 'Welcome.', photoUrl: PHOTO }));
    assert.ok(nonBackgroundPixels(png, 64, 760, 900, 940) > 1500, `${firstName} drew nothing in the name band`);
  }
});

test('a photo card without a photo is refused', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  await assert.rejects(renderCard({ variant: 'photo', firstName: 'Rivka', caption: 'x' }), /photo_missing/);
});

test('a photo from a host outside the allowlist is refused before any fetch', async () => {
  const { renderCard } = load('../src/lib/spotlight-card.tsx');
  await assert.rejects(renderCard({ variant: 'photo', firstName: 'Rivka', caption: 'x', photoUrl: 'https://evil.example/p.jpg' }), /photo_host_refused/);
});
```

If `next/og` refuses to load inside `vm.runInNewContext` (it reads WASM files relative to its own module path and uses `globalThis` features), fall back to running the renderer in the host realm: `load` returns a module built with `require` on a transpiled temp file written under `tests/.build/` instead of a sandbox. Record which path was needed in the report; the assertions do not change.

- [ ] **Step 3: Run, expect FAIL** (`Cannot find module ../src/lib/spotlight-card.tsx` and the text module).

- [ ] **Step 4: Implement `spotlight-card-text.ts`**

```ts
const LINK = /https?:\/\/\S+/g;
const MAX = 120;

/** The card's one caption line: the first sentence of the social caption, links removed, at most 120 characters. */
export function captionLine(caption: string): string {
  const text = caption.replace(LINK, '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  const m = /^(.+?[.!?])(\s|$)/.exec(text);
  let line = (m ? m[1] : text).trim();
  if (line.length > MAX) {
    const cut = line.slice(0, MAX);
    const at = cut.lastIndexOf(' ');
    line = (at > 40 ? cut.slice(0, at) : cut).replace(/[\s,;:]+$/, '') + '\u2026';
  }
  return line;
}
```

- [ ] **Step 5: Implement `spotlight-card-layout.tsx`** as a transcription of the SOT measurements (spec board in `Ahavah Spotlight Card Template.html`). Every `div` with more than one child carries `display: 'flex'` (satori requirement). Values:

```tsx
import type { ReactElement } from 'react';
import { captionLine } from './spotlight-card-text';

export type CardInput =
  | { variant: 'photo' | 'member_of_week'; firstName: string; age?: number; country?: string; caption: string; photoUrl?: string | null }
  | { variant: 'roundup'; tiles: Array<{ first_name: string; photo_url: string | null }>; caption: string }
  | { variant: 'roundup_fallback'; count: number; countries: number; caption: string };

export type Assets = { logo: string; photos: Map<string, string> };

const INK_ON_PHOTO = '#FFFFFF';
const COUNTRY = '#D9CFF7';
const CANVAS = '#1A1340';
const TILE = '#241A52';
const CHIP_BG = '#BC96FF';
const CHIP_INK = '#1A1340';
const LIME = '#D7FF81';
const NAME_FONT = 'Ultra, "Noto Serif Hebrew"';
const BODY_FONT = '"Plus Jakarta Sans"';
const GRADIENT = 'linear-gradient(to top, rgba(11,8,32,0.94) 0%, rgba(11,8,32,0.82) 18%, rgba(11,8,32,0.42) 44%, rgba(11,8,32,0) 100%)';

function chipText(input: CardInput): string {
  if (input.variant === 'member_of_week') return 'Member of the week';
  if (input.variant === 'photo') return 'New on Ahavah';
  return 'Spotlight';
}

function Mark({ logo }: { logo: string }) {
  return <img src={logo} width={96} height={96} style={{ position: 'absolute', left: 64, top: 64 }} />;
}

function Chip({ text }: { text: string }) {
  return (
    <div style={{ position: 'absolute', right: 64, top: 64, height: 64, padding: '0 32px', borderRadius: 999, background: CHIP_BG, color: CHIP_INK, fontFamily: BODY_FONT, fontSize: 30, fontWeight: 700, letterSpacing: '-0.01em', display: 'flex', alignItems: 'center' }}>{text}</div>
  );
}

function PhotoCard(input: Extract<CardInput, { variant: 'photo' | 'member_of_week' }>, assets: Assets): ReactElement {
  const photo = input.photoUrl ? assets.photos.get(input.photoUrl) : undefined;
  const name = input.age != null ? `${input.firstName}, ${input.age}` : input.firstName;
  return (
    <div style={{ width: 1080, height: 1080, background: CANVAS, position: 'relative', display: 'flex', overflow: 'hidden' }}>
      {photo ? <img src={photo} width={1080} height={1080} style={{ position: 'absolute', left: 0, top: 0, objectFit: 'cover' }} /> : null}
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 640, backgroundImage: GRADIENT }} />
      <Mark logo={assets.logo} />
      <Chip text={chipText(input)} />
      <div style={{ position: 'absolute', left: 64, right: 64, bottom: 64, display: 'flex', flexDirection: 'column' }}>
        {input.variant === 'member_of_week' ? <div style={{ width: 120, height: 5, borderRadius: 3, background: LIME, marginBottom: 26 }} /> : null}
        <div style={{ fontFamily: NAME_FONT, fontSize: 104, lineHeight: 0.98, letterSpacing: '-0.02em', color: INK_ON_PHOTO }}>{name}</div>
        {input.country ? <div style={{ fontFamily: BODY_FONT, fontSize: 40, fontWeight: 600, lineHeight: 1.2, color: COUNTRY, marginTop: 14 }}>{input.country}</div> : null}
        {captionLine(input.caption) ? <div style={{ fontFamily: BODY_FONT, fontSize: 32, fontWeight: 500, lineHeight: 1.35, color: 'rgba(255,255,255,0.86)', marginTop: 22, maxWidth: 820, lineClamp: 2 }}>{captionLine(input.caption)}</div> : null}
      </div>
    </div>
  );
}
```
Roundup: headline `New this week` (Ultra 96 / 0.96, left 64, top 170), grid at left 64, top 330, 952 by 476, tiles 468 by 230 radius 20 with the bottom 46 percent scrim `linear-gradient(to top, rgba(11,8,32,0.9), rgba(11,8,32,0))` and the name at left 22, bottom 18 (Plus Jakarta Sans 34 / 700). With 3 tiles: a 2 by 2 grid with the fourth cell empty is not in the SOT; the SOT says "2 by 1 then 1 by 1, same outer box, tiles stretch to fill": 4 tiles = 2 by 2; 3 tiles = a 2 by 1 top row and a full-width bottom tile; 2 tiles = 2 by 1 at full height; 1 tile = one tile filling the box. Satori has no grid, so lay tiles out with nested flex rows and explicit widths. Count line at left 64, bottom 64 (Plus Jakarta Sans 38 / 600, `#D9CFF7`): `and ${count - tiles.length} more across ${countries} countries` when `count > tiles.length`, else `${countries} countries` is wrong for zero; render `New members this week` when nothing remains. Fallback: the 952 by 476 box at radius 24 with a 2 px `rgba(255,255,255,0.12)` border, filled by an `<img>` whose `src` is a generated SVG data URI (56 px dot grid of `r=3.5` circles at `rgba(255,255,255,0.14)`, four pins at (176,150) lime, (420,300) lavender, (636,120) lavender, (800,330) lime, r 15 with r 34 rings at 35 percent alpha), and the count line `${count} new members across ${countries} countries`.

- [ ] **Step 6: Implement `spotlight-card.tsx`**

```tsx
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { ImageResponse } from 'next/og';
import { cardElement, type CardInput } from './spotlight-card-layout';
export type { CardInput } from './spotlight-card-layout';

const FONTS: Array<{ name: string; file: string; weight: number }> = [
  { name: 'Ultra', file: 'Ultra-Regular.ttf', weight: 400 },
  { name: 'Plus Jakarta Sans', file: 'PlusJakartaSans-Medium.ttf', weight: 500 },
  { name: 'Plus Jakarta Sans', file: 'PlusJakartaSans-SemiBold.ttf', weight: 600 },
  { name: 'Plus Jakarta Sans', file: 'PlusJakartaSans-Bold.ttf', weight: 700 },
  { name: 'Noto Serif Hebrew', file: 'NotoSerifHebrew-Black.ttf', weight: 900 },
];
let fontsPromise: Promise<Array<{ name: string; data: ArrayBuffer; weight: number; style: 'normal' }>> | null = null;
let logoPromise: Promise<string> | null = null;
const MAX_PHOTO_BYTES = 6_000_000;
const PHOTO_TIMEOUT_MS = 10_000;

function allowedHosts(): Set<string> {
  return new Set((process.env.AHAVAH_PHOTO_HOSTS || 'user-images.ahavah.app').split(',').map(s => s.trim()).filter(Boolean));
}

async function loadFonts() { /* readFile each FONTS entry once; ArrayBuffer via buf.buffer.slice(byteOffset, byteOffset + byteLength) */ }
async function loadLogo() { /* readFile assets/brand/logo-mark-lime.svg, return 'data:image/svg+xml;base64,...' */ }

async function fetchPhoto(url: string): Promise<string> {
  if (url.startsWith('data:image/')) return url;
  const host = new URL(url).hostname;
  if (!allowedHosts().has(host)) throw new Error('photo_host_refused');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PHOTO_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) throw new Error(`photo_fetch_${res.status}`);
    const bytes = Buffer.from(await res.arrayBuffer());
    if (bytes.length > MAX_PHOTO_BYTES) throw new Error('photo_too_large');
    const type = res.headers.get('content-type')?.split(';')[0] || 'image/jpeg';
    return `data:${type};base64,${bytes.toString('base64')}`;
  } finally { clearTimeout(timer); }
}

export async function renderCard(input: CardInput): Promise<Buffer> {
  const urls: string[] = [];
  if (input.variant === 'photo' || input.variant === 'member_of_week') {
    if (!input.photoUrl) throw new Error('photo_missing');
    urls.push(input.photoUrl);
  } else if (input.variant === 'roundup') {
    for (const t of input.tiles) if (t.photo_url) urls.push(t.photo_url);
  }
  const [fonts, logo, ...photos] = await Promise.all([fontsPromise ??= loadFonts(), logoPromise ??= loadLogo(), ...urls.map(fetchPhoto)]);
  const map = new Map(urls.map((u, i) => [u, photos[i]]));
  const res = new ImageResponse(cardElement(input, { logo, photos: map }), { width: 1080, height: 1080, fonts });
  return Buffer.from(await res.arrayBuffer());
}
```
`data:` photo URLs are accepted only so tests can inject pixels; production rows always carry `https://user-images.ahavah.app/...` (see `service/spotlight/eligibility.py` `photo_url`).

- [ ] **Step 7: Tofu check.** After rendering, satori leaves a glyph missing from every loaded font as an empty box. Before returning, `renderCard` runs `cardElement` text through a coverage check: load the font files with `opentype`-free logic by reading the `cmap` through satori's own font loader is not exposed, so implement the check on the input instead: every character of `firstName` (after NFC normalisation) must be in one of three ranges the fonts cover (Latin plus Latin Extended: U+0000 to U+024F and U+1E00 to U+1EFF and combining marks U+0300 to U+036F; Hebrew: U+0590 to U+05FF); anything else throws `Error('name_glyphs_unsupported')`. Test: a Cyrillic name rejects with that error.

- [ ] **Step 8: Wire the tick** (no code change expected: `tick.ts` imports `./spotlight-card`, the new `.tsx` resolves the same specifier). Run `tests/tick.test.mjs` unchanged; its `renderCard` injection still works.

- [ ] **Step 9: Visual gate.** Write `scripts/render-card-samples.mjs` that calls `renderCard` for the six SOT cases (welcome Rivka, highlight Daniel, member of the week Aaliyah, roundup four, roundup fallback, Hebrew name) with the stock photos from `ahavah-web/Claude Design/assets/stock/` served as `data:` URIs, writing `tests/_evidence/cards/*.png`. Open each beside the SOT frame; attach all six PNGs to the report. The reviewer compares against `Ahavah Spotlight Card Template.html` rendered at 1080.

- [ ] **Step 10: Gates and commit.** `node --test tests/*.test.mjs` (59 + 9), `npx tsc --noEmit`, `npx next build`. Commit `feat(admin): spotlight card renderer from the designed template`.

---

### Task 3: Privacy switch row (web)

**Files:**
- Modify: `src/app/settings/privacy/page.tsx`
- Create: `src/lib/spotlight-copy.ts`
- Test: `tests/lib/privacy-spotlight.test.tsx`

**Interfaces:**
- Consumes: `GET /profile-info` returns `spotlight_opt_in: boolean` (`service/person/sql/__init__.py` line 2159); `PATCH /profile-info {spotlight_opt_in: boolean}` (`service/person/__init__.py` line 1986; turning it off runs the withdrawal operation server-side).
- Produces: `SPOTLIGHT_COPY` object with `privacy.sectionLabel = 'Spotlight'`, `privacy.title = 'Feature me in Spotlight'`, `privacy.description = 'Your first name, age, country and one photo you choose, on our Facebook page, Instagram and the weekly email. You approve each card first.'` (verbatim from the SOT), plus the confirm and card strings Tasks 4 and 5 add.

- [ ] **Step 1: Failing test** (mirror `tests/lib/privacy-save.test.tsx`: mock `@/lib/api-client`, `SettingsShell`, `Switch`)

```tsx
it('renders the Spotlight row off by default and PATCHes spotlight_opt_in', async () => {
  get.mockResolvedValue({ spotlight_opt_in: false, ahavah_extra: {} });
  patch.mockResolvedValue({});
  render(<PrivacyPage />);
  const sw = await screen.findByRole('switch', { name: 'Feature me in Spotlight' });
  expect(sw).toHaveAttribute('aria-checked', 'false');
  expect(screen.getByText('Spotlight')).toBeInTheDocument();
  fireEvent.click(sw);
  await waitFor(() => expect(patch).toHaveBeenCalledWith('/profile-info', { spotlight_opt_in: true }));
  await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
});
it('keeps the previous value when the PATCH fails', async () => { /* patch rejects, aria-checked stays false, error line shown */ });
```

- [ ] **Step 2: Run, expect FAIL** (no switch named that way).

- [ ] **Step 3: Implement.** Add `spotlight` state (`useState(false)`), read `p.spotlight_opt_in === true` on load, `saveSpotlight(next)` following `saveMapVisibility` exactly (busy guard, non-optimistic, `apiClient.patch("/profile-info", { spotlight_opt_in: next })`, error text "Could not save. Your previous setting still applies. Please try again."). Render a new `<section>` between the Location and Profile sections:

```tsx
<section className="flex flex-col gap-2">
  <h2 className="px-3 text-overline text-(--ink-3)">{SPOTLIGHT_COPY.privacy.sectionLabel}</h2>
  <ItemGroup className="gap-1">
    <Item variant="muted">
      <ItemContent>
        <ItemTitle className="text-meta text-(--ink)">{SPOTLIGHT_COPY.privacy.title}</ItemTitle>
        <ItemDescription className="text-caption text-(--ink-3)">{SPOTLIGHT_COPY.privacy.description}</ItemDescription>
      </ItemContent>
      <Switch checked={spotlight} disabled={!privacyLoaded || Boolean(savingKey) || mapSaving || spotlightSaving} onCheckedChange={(c) => void saveSpotlight(c)} aria-label={SPOTLIGHT_COPY.privacy.title} />
    </Item>
  </ItemGroup>
</section>
```
Not Gold-gated. No lavender inset rule (document-only in the SOT).

- [ ] **Step 4: Gates and screenshots.** `pnpm test`, `tsc`, eslint. Screenshots at 390 dark and light of `/settings/privacy` signed in as a test member against the local API; compare with the SOT "Privacy spotlight row dark/light" frames.

- [ ] **Step 5: Commit** `feat(privacy): feature me in Spotlight switch`.

---

### Task 4: Confirm page (web)

**Files:**
- Create: `src/components/app/spotlight-shell.tsx`, `src/app/spotlight/confirm/[token]/page.tsx`
- Modify: `src/lib/spotlight-copy.ts`
- Test: `tests/app/spotlight-confirm.test.tsx`

**Interfaces:**
- Consumes: `GET /spotlight/confirm/<token>` answers `{email_masked, already, stale}`; 400 invalid; 410 expired. `POST /spotlight/confirm/<token>` answers `{ok: true, already: false}` (fresh), `{ok: true, already: true}`, 410 `{error: 'stale'}`, 400 invalid. Calls go through `apiClient` without a session (the claim page pattern).
- Produces: `SpotlightShell({ children, wide?: boolean })`: `min-h-dvh bg-(--app)`, brand row (`LogoMark size={28}` beside the word "Ahavah" at 17 px weight 800, tracking -0.02em) 58 px tall inside 24 px side padding on mobile; on `lg:` the SOT desktop frame: an 80 px top bar with a hairline under it and a centred 660 px `bg-(--card)` card with a hairline border, radius 26, padding 44 by 48. No bottom nav, no footer links. `ConfirmState = 'loading' | 'default' | 'already' | 'success' | 'invalid' | 'expired' | 'error'`.

- [ ] **Step 1: Copy** (append to `spotlight-copy.ts`, verbatim from the SOT):
  - chip `Spotlight`; headline `Feature me in Spotlight.` with `Spotlight` in lime (`<em>`); paragraph `We will show your first name, age, country and one photo you choose on the Ahavah Facebook page, Instagram and the weekly community email. You approve every card before it goes out. Turn it off any time in Settings, Privacy.`; mail label `Sent to`; button `Feature me in Spotlight`; footer `This page has not changed anything yet.`
  - already: `You are already in Spotlight.` / `Nothing more to do here. We will email you before anything is posted, and you can turn Spotlight off any time in Settings, Privacy.` / link `Open Settings, Privacy`.
  - success: `You are in. We will email you before anything is posted.` / `Your first name, age, country and one photo you choose can now appear on the Ahavah Facebook page, Instagram and the weekly community email. Every card comes to you for approval first.` / link `Open Settings, Privacy`.
  - invalid: `This link is not valid.` / `It may have been copied incompletely. Turn on Spotlight in Settings, Privacy instead.` / ghost button `Open Settings, Privacy`.
  - expired: `This link has expired.` / `Turn on Spotlight in Settings, Privacy instead.` / lime button `Open Settings, Privacy`.
  - error (network, not in the SOT, reuse the invalid layout with): `We could not reach Ahavah.` / `Check your connection and open the link again.` / ghost button `Try again`.

- [ ] **Step 2: Failing tests** (`tests/app/spotlight-confirm.test.tsx`, mock `@/lib/api-client` with `vi.hoisted`; render with `params: Promise.resolve({ token: 't1' })`):
  - default: GET resolves `{email_masked: 'r••••a@gmail.com', already: false, stale: false}` → heading "Feature me in Spotlight.", the masked email, the button, the footer line; `post` not called on load.
  - clicking the button POSTs once and shows the success heading; the button is gone.
  - already: GET `already: true` → "You are already in Spotlight." and no button.
  - stale: GET `stale: true` → expired layout.
  - GET rejects with `ApiError` status 410 → expired; status 400 → invalid; network error → error state.
  - POST rejects 410 → expired.

- [ ] **Step 3: Run, expect FAIL.**

- [ ] **Step 4: Implement.** `"use client"`; `const { token } = use(params)`; `useEffect` with a `ranRef` guard does the GET; state machine as above; the POST runs only from the button's `onClick`, disabled while pending. Layout per the SOT: chip = `<Badge variant="lavender">`, headline `text-[34px] leading-[1.08] tracking-[-0.01em]` in `var(--font-display)` (52 px on `lg:`), `em` with `text-(--color-lime)` (light theme `#4a7a00` via a class toggled on `[data-theme=light]`, the SOT value), paragraph `text-[14.5px] leading-[1.55] text-(--ink-2)`, mail row `rounded-[14px] bg-(--sunk) border border-(--hairline) p-[13px_15px]` with a `Mail` icon, label `text-[11px] font-bold tracking-[0.09em] uppercase text-(--ink-3)`, value `text-[14px] font-bold`; primary `Button size="cta" tone="cta"` (56 px lime pill), ghost `Button variant="outline" size="cta"`; success and already use a 60 px round `badgeico` (lime at 18 percent) with a `Check` icon; invalid uses `AlertCircle`, expired uses `Clock`, both gold at 16 percent. Settings links go to `/settings/privacy`. If `--sunk` does not exist in `globals.css`, use `bg-white/[0.035]` dark and `bg-[rgba(15,11,31,0.025)]` light.

- [ ] **Step 5: Gates and screenshots** at 390 (five states, by stubbing the API with `?state=` is not allowed on a public page; instead point the dev app at the local API and mint tokens with `spotlight_confirm_url` in a Python shell inside the API container for each state) and 1440 (default, success). Compare with the SOT frames.

- [ ] **Step 6: Commit** `feat(spotlight): confirmation page`.

---

### Task 5: Card approval page (web)

**Files:**
- Create: `src/app/spotlight/card/[token]/page.tsx`
- Modify: `src/lib/spotlight-copy.ts`
- Test: `tests/app/spotlight-card.test.tsx`

**Interfaces:**
- Consumes: `GET /spotlight/card/<token>` → `{first_name, age, country, kind, caption, photos, photo_uuid, revision, preview_available, image_url, status: 'approved' | 'skipped' | 'awaiting_member', expires_at, stale}`; 400 invalid, 404 not found, 410 expired. `POST /spotlight/card/<token> {decision: 'approve', photo_uuid}` → `{ok, result: 'approved'}` or `{ok, already, status}`; `{decision: 'skip'}` → `{ok}`; 409 `{error: 'approvals_disabled' | 'preview_unavailable' | ...}`; 410 `{error: 'stale'}`.
- Produces: `CardState = 'loading' | 'default' | 'approved' | 'skipped' | 'unavailable' | 'paused' | 'invalid' | 'expired' | 'error'`. Reuses `SpotlightShell`.

- [ ] **Step 1: Copy** (owner decision 9; sentence case, no em dashes):
  - default: chip `Spotlight`; headline `Your Spotlight card is ready.`; paragraph `This is the card we will post on the Ahavah Facebook page and Instagram, with your first name, age and country. Approve it and we schedule it. Skip it and nothing is posted.`; the rendered card image (`image_url`, square, radius 20, full width, `alt` "Your Spotlight card"); primary `Approve this card`; ghost `Skip this card`; footer `This page has not changed anything yet.`
  - approved: `Approved. We will email you when it is live.` / `Your card is on its way to the queue. You can turn Spotlight off any time in Settings, Privacy.` / link `Open Settings, Privacy`.
  - skipped: `Skipped. Nothing will be posted.` / `You can still be featured later. Turn Spotlight off in Settings, Privacy if you would rather not.` / link `Open Settings, Privacy`.
  - unavailable (`preview_available` false or `image_url` null): `Your card is still being prepared.` / `We will email you again when it is ready to look at.` / no button.
  - paused (409 `approvals_disabled`): `Approvals are paused for a moment.` / `Nothing has changed. We will email you when this card can be approved.`
  - invalid, expired, error: the confirm page states with the same copy.

- [ ] **Step 2: Failing tests** (`tests/app/spotlight-card.test.tsx`): default shows the image and both buttons and does not POST on load; Approve POSTs `{decision: 'approve', photo_uuid: '<from GET>'}` once and shows the approved heading; Skip POSTs `{decision: 'skip'}`; GET `status: 'approved'` lands on the approved state directly; `preview_available: false` → unavailable; POST 409 `approvals_disabled` → paused; GET 410 → expired; 400 → invalid.

- [ ] **Step 3: Run, expect FAIL.**

- [ ] **Step 4: Implement** as Task 4, with the card image between the paragraph and the buttons (`<img>` not `next/image`: the URL is presigned and short-lived). Both buttons disabled while a POST is pending.

- [ ] **Step 5: Gates and screenshots** at 390 and 1440 for default, approved and skipped, against a locally rendered card (Task 2 sample PNG uploaded through the attach route on the local stack). Attach.

- [ ] **Step 6: Commit** `feat(spotlight): card approval page`.

---

### Task 6: Growth tab, data layer and the Stats and Queue sections (admin)

**Files:**
- Create: `src/lib/growth-api.ts`, `src/lib/growth-queries.ts`, `src/lib/growth-mutations.ts`, `src/components/ui/switch.tsx`, `src/components/admin/tab-growth.tsx`, `growth-stats.tsx`, `growth-queue.tsx`, `growth-removals.tsx`
- Modify: `src/lib/tabs.ts`, `src/components/admin/tab-router.tsx`, `src/lib/types.ts`
- Test: `tests/growth-api.test.mjs`

**Interfaces:**
- Consumes (all through `api` from `src/lib/api-client.ts`, session bearer, `/api` proxy): `GET /admin/growth/stats` → `{members_by_gender: [{gender, members, new_7d, new_30d, acted_14d, stale_30d, never_acted, with_photo, premium, opted_in}], matches, matches_30d, likes_total, likes_7d, msgs_7d, msgs_30d, opted_in}`; `GET /admin/growth/queue` → rows with the Task 1 fields; `GET /admin/growth/token-health` → `{expires_at, valid, days_left}`; `GET /admin/growth/removals?pending=1` → `{tasks: [{id, platform, external_post_id, reason, attempts, last_error, next_attempt_at, deadline_at, evidence, ...}], halted, overdue, outstanding_cleanup, abandoned_cleanup, render_blocked}`; `POST /admin/growth/removals/<id>/done`; `POST /admin/growth/queue/<id>/approve {scheduled_for?}`, `/cancel {reason?}`, `/retry`, `/reschedule {scheduled_for}`, `/caption {caption}`; `GET /admin/whoami` (existing, for the email).
- Produces (pure, tested): `groupQueue(rows: QueueRow[]): QueueCard[]` (one card per `request_key`, `platforms: Array<{platform, id, status, delivery_state, error, post_url}>`, `status` = the shared status when equal else `'mixed'`, `thumb = first preview_url`, `clicks`/`signups` from the first row, `scheduled_for` = earliest), `statusBadge(status): { label, tone }` with the SOT labels (`awaiting_member` → "Awaiting member" pending, `awaiting_render` → "Rendering" pending, `review` → "Review" pending, `scheduled` → "Scheduled" admin, `processing` → "Processing" admin, `published` → "Published" active, `failed` → "Failed" deactivated, `cancelled` → "Cancelled" neutral), `kindBadge(kind)` ("Welcome" mod, "Roundup" neutral, "Member of the week" premium, "Highlight" mod), `barbados(iso: string): string` ("Mon 15 Sep, 08:00" in America/Barbados via `Intl.DateTimeFormat`), `tokenChip(health): { label, tone: 'green' | 'amber' | 'red' }` ("Token OK, N days" when valid and N > 14; "Expires in N days" amber when 14 or fewer; "Token invalid" red when not valid), `cardsWaiting(rows)` = count of distinct `request_key` with every row `scheduled`.

- [ ] **Step 1: Failing tests** (`tests/growth-api.test.mjs`, same `vm` transpile harness as `tests/growth-server.test.mjs`, no `fetch` needed for the pure helpers): grouping merges two platform rows into one card with two platform chips and `status: 'published'`; a published Facebook row with a failed Instagram row gives `status: 'mixed'`; `tokenChip({valid: true, days_left: 212})` → "Token OK, 212 days" green; `days_left: 9` → amber "Expires in 9 days"; `valid: false` → red; `barbados('2026-09-15T12:00:00Z')` → "Tue 15 Sep, 08:00"; `cardsWaiting` counts only fully scheduled cards.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement the data layer.** `growth-api.ts` holds the types and pure helpers (no React). `growth-queries.ts`: `useGrowthStats`, `useGrowthQueue` (refetch every 60 s), `useTokenHealth`, `useRemovals`, `useGrowthSettings`, `useGrowthEmails`, `useSuggest` as `useQuery` hooks keyed `['growth', ...]`. `growth-mutations.ts`: `useQueueAction(action: 'approve' | 'cancel' | 'retry' | 'reschedule')`, `useRemovalDone`, `useSetting`, `usePurge`, `useMemberOfWeek`, `useEmailSend`, `useEmailPreview`, each invalidating `['growth']`. Register the tab: `tabs.ts` adds `{ id: "growth", label: "Growth", icon: Megaphone }` after `moderation` (the SOT sidebar order: Dashboard, Cohorts, Users, Moderation, Economy, Growth, Audit log, System; keep the existing registry order and insert Growth after Economy). `tab-router.tsx` adds the case.

- [ ] **Step 4: Switch primitive.** `src/components/ui/switch.tsx` on `@radix-ui/react-switch`: 42 by 24 track `bg-(--card-3) border border-(--line-2)`, checked `bg-(--lime) border-(--lime)`, 16 px thumb `bg-(--muted)`, checked thumb `bg-[#0F0B1F]` translated 18 px (SOT `.sw`).

- [ ] **Step 5: Stats section** (`growth-stats.tsx`). `SectionLabel` "Stats" with the right slot "Updated N minutes ago" (from the query's `dataUpdatedAt`). Two `AdminCard`s in a two-column grid (`grid-cols-1 md:grid-cols-2 gap-3.5`): "Members by gender" table with Men and Women columns and the nine SOT rows (Members, Joined 7 days, Joined 30 days, Acted 14 days, Stale 30 days, Never acted, With photo, Premium, Opted in), and "Totals" (Matches, Matches 30 days, Likes 7 days, Messages 7 days, Messages 30 days, Opted in, Approved cards waiting). Table cells per the SOT `.gtbl`: header `10.5px 800 tracking 0.1em uppercase text-(--faint)`, key cells `text-(--muted) font-semibold`, numbers right-aligned `text-(--ink) font-bold tabular-nums` 88 px wide. Below, the KPI strip with the six SOT tiles (Opted in "of N members", Joined 7 days "Men a, women b", Acted 14 days "Liked or messaged", Stale 30 days "Re-invite candidates", Matches "N in 30 days", Cards waiting "Approved, not posted") using a local `KStrip` that matches the SOT `.kstrip` (one bordered strip, items separated by hairlines, Ultra 30 px numbers) rather than the existing `KpiStrip` grid, because the SOT strip is one continuous bar. Loading: the SOT skeleton (three `skel-kpi` blocks, then the two cards with five `sk` bars each). Men and women map from `members_by_gender` by `gender` name (`Man`/`Woman` values as the API returns them; check `gender.name` values in `init-api.sql` and map both spellings).

- [ ] **Step 6: Queue section** (`growth-queue.tsx`). `SectionLabel` "Spotlight queue" with the token chip and a ghost "Refresh" button on the right. When `token.valid === false`, the SOT danger callout above the table: "**The Facebook page token is invalid.** Nothing will post until it is replaced. Scheduled rows stay in the queue and are retried once a new token is saved. Replace it in System, Integrations." `TableShell` + `table` with `Th` columns: Card (52 px thumbnail, `preview_url`, radius 7, hairline border), Kind, Member (first name; roundup shows "N members" in `text-(--faint)`), Platforms (chips per SOT `.chip`), Scheduled, Barbados, Status, Clicks, Sign-ups, and a 46 px actions column with an icon button opening a Radix dropdown (`@radix-ui/react-dropdown-menu` is a dependency) with the five actions: Approve (shown for `review` and `awaiting_render` when `consent_complete` and a render exists; disabled with the reason "Waiting for the member" when `awaiting_member`), Post now (for `scheduled`, `review` after approve; calls reschedule with the current ISO time), Reschedule (opens a `datetime-local` dialog; converts from Barbados to UTC), Cancel (for anything not `published`/`cancelled`; confirmation dialog), Copy caption and open post (copies `caption` to the clipboard with `navigator.clipboard.writeText` and opens `post_url` in a new tab; disabled without `post_url`). A `failed` card renders the SOT error row under it (`err-note` with the `AlertCircle` icon and the row's `error` text plus "Attempt N of 3"). `delivery_state === 'delivery_unknown'` shows an amber "Delivery unknown" badge next to the status badge and the menu offers only "Cancel" and "Copy caption and open post"; the reconcile route stays a curl-level operation this wave (record in the ledger). Table foot: "N rows. Row menu: Approve, Post now, Reschedule, Cancel, Copy caption and open post." left and "All times Barbados, UTC minus 4" right. Empty: the SOT `EmptyState` with the inbox icon, "Nothing in the queue", the hint "Welcome cards are queued automatically when an opted-in member completes their profile. You can also start one from New spotlight." and a lavender "New spotlight" button that scrolls to the Member of the week section. Loading: the SOT four skeleton rows under the real header.

- [ ] **Step 7: Removals panel** (`growth-removals.tsx`). `AdminCard` titled "Remove by hand" with the sub "Instagram has no delete endpoint for these". One `task` row per pending task whose `reason` is `manual_instagram` or `investigate`: a checkbox (Radix checkbox styled per the SOT `.checkbox`, lavender when on), the text "Remove <kind> for <first name or the request key> from Instagram, requested <date>" (for `investigate`: "Check whether <request key> was posted on <platform>; delivery was unresolved when the member withdrew"), and the `external_post_id` in `text-(--faint)` on the right. Ticking calls `removalDone` and the row renders struck through with "Done <date>" until the list refreshes. Overdue tasks (`deadline_at` in the past) carry a `Badge tone="deactivated"` "Overdue". Header right slot shows counts from the response when non-zero: "N overdue", "N cleanup outstanding", "N abandoned" as small chips. Empty: "Nothing to remove" / "Instagram removals appear here when a member withdraws consent."

- [ ] **Step 8: Page head.** `PageHead title="Growth" sub="Spotlight queue, community emails and the numbers behind both."` with the right slot: ghost "Export csv" (downloads the grouped queue as CSV via a Blob, columns kind, member, platforms, scheduled_for, status, clicks, signups) and lavender "New spotlight" (scrolls to Member of the week). Mobile (`max-md`): the SOT read-only note ("Read-only on mobile. Approving, posting, sending and purging need a desktop.") and the stacked `mrow` layout for the queue; action menus hidden.

- [ ] **Step 9: Gates and screenshots.** `node --test`, `tsc`, `next build`. Run the admin against the local API stack with a seeded queue (one row per SOT status: published, scheduled, awaiting member, review, failed with an error, cancelled, processing) and screenshot at 1440 (full, loading, empty with token invalid) and 390. Attach.

- [ ] **Step 10: Commit** `feat(admin): Growth tab with stats, queue and removals`.

---

### Task 7: Growth tab, Member of the week, Emails and Controls sections plus dialogs (admin)

**Files:**
- Create: `src/components/admin/growth-motw.tsx`, `growth-emails.tsx`, `growth-controls.tsx`, `growth-dialogs.tsx`
- Modify: `src/components/admin/tab-growth.tsx`, `src/lib/growth-api.ts` (helpers), `tests/growth-api.test.mjs`

**Interfaces:**
- Consumes: `GET /admin/growth/spotlight/suggest` → `[{person_id, first_name, age, country, photo_url, reason, suggested_caption}]`; `POST /admin/growth/spotlight/member-of-week {person_id, scheduled_for, caption}` → `{request_key}`; `GET /admin/growth/emails` → five campaigns (Task 1); `POST /admin/growth/emails/<c>/preview {to}`; `POST /admin/growth/emails/<c>/send {campaign_id, dry_run}` → run result with counts; `GET /admin/growth/emails/<c>/status/<id>`; `GET /admin/growth/settings` and `POST /admin/growth/settings {key, value: 'true' | 'false'}`; `POST /admin/growth/queue/purge` → `{cancelled, left_attempting}`.
- Produces (pure, tested): `campaignId(now: Date, campaign: 'e1' | 'e2' | 'e3'): string` = `cmp_<isoYear>w<isoWeek two digits>_<spotlight | comm | reinv>` (the SOT ids); `nextMondayNoonUtc(now: Date): string` (ISO); `purgeBreakdown(rows): { total, byStatus: Record<string, number> }` counting cards not `published` and not `cancelled`, grouped by SOT label; `captionCount(text): number` (code points).

- [ ] **Step 1: Failing tests** for the four helpers: `campaignId(new Date('2026-09-15T12:00:00Z'), 'e1')` → `cmp_2026w38_spotlight`; `nextMondayNoonUtc` from a Tuesday → the following Monday 12:00 UTC, from a Monday 11:00 UTC → the same day 12:00; `purgeBreakdown` on the seeded queue → `{total: 5, byStatus: {'awaiting member': 2, review: 1, scheduled: 1, failed: 1}}`; `captionCount('a\u{1F600}')` → 2.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Member of the week** (`growth-motw.tsx`). `SectionLabel` "Member of the week" with "Next slot <Monday date>" on the right. The SOT `.motw` grid (288 px card + editor; one column on mobile). Suggested card: square photo, "First name, age", country, reason line. Two alternatives as the SOT `.alt` rows; clicking one makes it the pick and moves the previous pick into the alternatives ("Pick someone else" cycles to the next alternative). Caption editor (`textarea` per SOT `.textarea`, min height 118) prefilled from `suggested_caption`, the counter "N of 2200" (over 2200 disables Queue) and the hint "Plain text only. Links are counted by Facebook as characters." Schedule select: options for the next four Mondays at 12:00 UTC (label "Monday 12:00 UTC", hint "Monday 08:00 in Barbados"). Platforms: static text "Facebook and Instagram" with the hint "Member approves the card before either posts." Buttons: lime "Queue for approval" (calls member-of-week, then toasts "Queued for <first name>. The card renders on the next tick." and invalidates the queue) and ghost "Pick someone else". Empty pool: `EmptyState` "No eligible member this week" / "Members become eligible when they opt in, have an approved photo and were not featured in the last 30 days."

- [ ] **Step 4: Emails** (`growth-emails.tsx`). `SectionLabel` "Emails" with "Dry run sends to the admin address only" on the right. Table columns Email, Recipients, Last sent, Last campaign id (mono), actions. Names: Spotlight announcement (e1), Weekly community email (e2), Re-invite (e3), Card ready (e4, "System sent" neutral badge), Card live (e5, "System sent"). Actions: ghost "Preview" (POST preview with `to` = whoami email; toast "Preview sent to <email>"), subtle "Dry run" (send with `dry_run: true`; toast with the counts the API returns), lime "Send" (e1 to e3 only; opens the send dialog). Last sent renders "Never" or "8 Sep, 12:04" (Barbados). After a real send, poll `status` every 5 s for 60 s and show "Queued N, accepted N" under the row; `acceptance_unknown > 0` shows a danger callout "N messages have unknown delivery. Check the outbox before sending again."

- [ ] **Step 5: Controls** (`growth-controls.tsx`, owner decision 5). Two `AdminCard`s. "Controls": three `swrow`s: "Invites" / "When off, no new welcome or roundup cards are created and no card-ready email is sent. Existing cards keep moving." (`invites_enabled`); "Publication" / "When off, nothing is claimed for posting. Rendering, approvals and removals continue." (`publication_enabled`); "External access" / "The emergency stop. When off, nothing is sent to Meta: no posts, no deletions. Turn it off first if anything looks wrong." (`external_access_enabled`, its switch uses the red tone when off: track `bg-(--red-soft) border-[rgba(255,69,102,0.32)]`). Under them two read-only chips: "Member approvals: on/off" (`approvals_enabled`) and "Roundup tiles: on/off" (`roundup_tiles_enabled`) with the hint "Set from the API when the renderer and the roundup approval flow are activated." Every switch flip posts `settings` and is confirmed by a toast; a failed post reverts. "Queue maintenance": the SOT paragraph, the amber callout "N rows would be cancelled: 2 awaiting member, 1 review, 1 scheduled, 1 failed." from `purgeBreakdown` (hidden when zero), danger-ghost "Purge queue" (opens the purge dialog; disabled when zero) and ghost "Open audit log" (`/?tab=audit`).

- [ ] **Step 6: Dialogs** (`growth-dialogs.tsx`, on `src/components/ui/dialog.tsx`). Send: lavender mail icon tile, title "Send the <email name lower case>", body "This sends once to every member who has not received this campaign. It cannot be recalled.", target block "N recipients" over the mono campaign id, actions ghost "Cancel" and lime "Send to N members" → `send {campaign_id, dry_run: false}`. Purge: red trash tile, title "Purge the spotlight queue", body "This cancels N rows: <breakdown>. Published posts are not touched, and members who were told their card is coming will not be notified. This cannot be undone.", a text field labelled "Type purge to confirm" that must equal `purge`, actions ghost "Cancel" and danger "Cancel N rows". Cancel-row: title "Cancel this card", body "The member is not notified. A published post is not touched." Reschedule: a `datetime-local` input labelled "New time, Barbados" with the UTC equivalent as the hint.

- [ ] **Step 7: Mobile.** Under `md:` the SOT 390 frame: read-only note, KPI strip in two columns (Men, Women, Opted in, Acted 14 days, Matches, Cards waiting with the SOT subs), Queue as stacked `mrow`s with the failed row's error note, Member of the week card only, Emails as rows "N recipients · <last sent>", Controls as rows "On/Off". No switches, no Send, no Purge, no menus.

- [ ] **Step 8: Gates and screenshots** at 1440 (send dialog open, purge dialog open) and 390 (full page). Attach.

- [ ] **Step 9: Commit** `feat(admin): Growth tab member of the week, emails, controls and dialogs`.

---

### Task 8: Documentation, evidence and memory

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-community-spotlight-design.md` (3.3 card caption line ruling; 3.7 fonts and the Hebrew substitute and the tofu rule; 3.8 items 2, 3, 5 as built; 7 photo picker deferred)
- Modify: `docs/superpowers/handovers/2026-09-13-community-spotlight-handoff.md` (section 13: Wave 3, branches and heads, the ten owner decisions, the activation step for `approvals_enabled`, the deploy order api then web then admin, the manual title PNG download list `assets/email-titles/title-{spotlight,reinvite,card-ready}{,-wht}.png` to `ahavah-web/public/email/`)
- Create: `docs/superpowers/plans/2026-09-15-spotlight-wave-3-evidence.md` (per surface: SOT frame, screenshot path, reviewer verdict; renderer sample PNGs; test totals)
- Modify: memory `ahavah-community-spotlight.md` (Wave 3 built, decisions, where the record lives)

- [ ] Steps: write, sweep for em dashes, commit `docs(spotlight): wave 3 record`.

---

### Task 9: Deploy (owner go) and activation checklist

- [ ] Owner pre-flight still open from Phase B: Meta app permissions for Page `1100237303180442` and Instagram `17841447302854202`; the Page token and `AHAVAH_META_PAGE_TOKEN`, `AHAVAH_FB_PAGE_ID`, `AHAVAH_IG_USER_ID`, `META_GRAPH_VERSION`, `CRON_SECRET`, `AHAVAH_GROWTH_CRON_SECRET`, `AHAVAH_API_ORIGIN` in the admin Vercel project; `AHAVAH_GROWTH_CRON_SECRET` on the droplet; the admin role on the owner's account. New for Wave 3: the six title PNGs copied by hand from the design project into `ahavah-web/public/email/` (binaries are never fetched through the bridge).
- [ ] Push order on owner go: api (`ahavah/main`, no migration; deploy check includes `docker ps` for the cron container), web (`master`), admin (`master`). Post-deploy: `/settings/privacy` shows the row; a minted confirm link renders the default state; `GET /api/growth/tick?dry=1` with the cron bearer reports counts; the Growth tab loads all five sections against production data with everything still dormant.
- [ ] Activation is not part of this wave: `approvals_enabled`, `publication_enabled`, `roundup_tiles_enabled` stay `false` until the Wave 4 staging acceptance run. Flipping `approvals_enabled` is the renderer's activation step (Phase B Task 5 note) and is the owner's call after seeing a real rendered card in the queue.

## Self-review record

- Spec coverage: 3.1 consent surfaces (Tasks 3, 4, 5), 3.5 E4 landing (Task 5) and E1 landing (Task 4), 3.7 rendering (Task 2), 3.8 items 1 to 5 (Tasks 6, 7), 7 out of scope (decisions 1 to 4), 9 rollout step 2 and 3 (this wave). Not covered on purpose: F10, F12 remainder, the picker, the staging run (headline list above).
- Placeholder scan: every step names files, values and copy; the roundup tile layouts for one to three tiles and the fallback motif are specified numerically; the tofu rule is specified by code point ranges.
- Type consistency: `CardInput` in Task 2 matches `tick.ts` `renderInputFor` field names (`first_name`, `photo_url` inside `tiles`; `firstName`, `photoUrl` at the top level) as the existing stub declares them; `QueueRow` gains `preview_url`, `post_url`, `delivery_state` in `src/lib/types.ts` for the browser client while `growth-server.ts`'s server type stays untouched; `SpotlightShell` is produced in Task 4 and consumed in Task 5; `SPOTLIGHT_COPY` is created in Task 3 and appended in Tasks 4 and 5.
