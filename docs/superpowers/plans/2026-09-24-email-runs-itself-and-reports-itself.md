Linear: TEC-1188

# The weekly email runs itself, and the dashboard reports it honestly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The weekly community email sends on a schedule rather than when someone remembers it, the outbox's real delivery numbers are visible to the operator, and the System tab stops printing a success rate nobody measured.

**Architecture:** No new mechanism is invented. The schedule is a cron module in the existing `service/cron/` registry, exactly like `emailoutbox` and `betareengagement`, and it earns its idempotency from the campaign id the admin frontend already computes (`cmp_<isoyear>w<NN>_community`) hitting `_Q_SAME_RUN`, which is the dedupe the runner already has. The visibility work adds fields to the one route the System tab already calls and one panel built from the design primitives already in use. The honesty fix deletes two literals.

**Tech Stack:** Python 3.11, psycopg 3, Flask (`ahavah-api`); Next.js 16, React 19, Tailwind v4, TanStack Query (`ahavah-admin`).

**Spec:** none. This is three defects found by audit on 2026-09-24, after e2 was sent by hand for the first time since it shipped.

## What we found, which is the reason for this plan

On 2026-09-24 the weekly community email (e2, `community_weekly`) was sent manually with the frequency cap overridden. It reached 26 members, all accepted, zero failures. It was **the first e2 send in the product's history**: `email_send_log` held zero e2 rows beforehand.

Three things came out of that.

1. **Nothing schedules e2.** It exists as `python -m emails.send_community_weekly` and as a Send button in Growth > Emails. The droplet crontab holds only the nightly backup, and `service/cron/__init__.py` registers no community-weekly loop. A "weekly" email has been weekly in name only.
2. **The outbox has no screen.** The run produced exact delivery data (`email_outbox.state`, `attempts`, `last_error`, the `acceptance_unknown` state). None of it is visible. Worse, `growth-emails.tsx:66` tells the operator "Check the outbox before sending again" and `growth-queries.ts:87` says "past that the admin can read the outbox itself". There is no such screen. Both sentences point at nothing.
3. **The System tab prints a number nobody measured.** `tab-system.tsx:97` renders a 44px green `"100%"` whenever `otp.sent_24h > 0`, and `tab-system.tsx:113` renders a literal `0` under "Failures". Both are hardcoded in the JSX. `Q_OTP_24H` counts rows in `duo_session`, which is OTP codes *issued*, not messages *delivered*, so no delivery success rate exists to report at all.

## What this plan does not do

- **It does not change what e2 says.** The template, the copy and the brand shell are untouched.
- **It does not add OTP delivery tracking.** That needs Resend webhook ingestion and is a separate plan. This plan stops the dashboard claiming to have it.
- **It does not schedule e1 or e3.** Both are deliberately operator-run: e1 announces a specific thing and e3 targets a chosen cohort. Only e2 is cadence-driven.
- **It does not auto-publish spotlight cards.** A card becomes `published` on an operator receipt (`service/spotlight/queue.py:275`), so e2 will keep rendering the newcomers-only variant in any week without one. That fallback already exists and already works.

## Global Constraints

- Pushing `ahavah/main` deploys production. Work on `email-schedule-and-visibility`; deploy only after review and on the owner's go.
- **A scheduled send that double-fires is worse than one that misses.** Every guard in this plan errs toward sending nothing twice.
- No em dashes on added lines. Sentence case throughout, including UI copy.
- No attribution trailers in any commit. Git email `admin@techbaseltd.com`.
- Never nest `api_tx`. No literal `%` in psycopg SQL.
- Add files to git by explicit path. Never `git add -A`. Never `git stash`.
- API tests: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm -v /d/Antigravity/ahavah-api:/app -e INSIDE_CONTAINER=1 --entrypoint bash api /app/tests/run.sh tests -q` (baseline 910).
- Admin tests: `npm test` in `ahavah-admin`, and `npx tsc --noEmit` must pass. A green test run with a failing `tsc` has happened on this project before.

---

### Task 1: The week's campaign id, computed the same way on both sides

**Files:** Create `service/campaigns/weekid.py`, create `tests/test_campaign_week_id.py`.

**Interfaces:**
- Produces: `week_campaign_id(now: datetime, campaign: str) -> str`, returning `cmp_<isoyear>w<NN>_<suffix>` with a zero-padded two-digit week. Suffixes: `e1` to `spotlight`, `e2` to `community`, `e3` to `reinvite`.

This is the whole idempotency story, so it lands first and alone. The admin frontend already computes this string in `ahavah-admin/src/lib/growth-api.ts:565`. The cron must compute the identical string, because that is what makes an operator's manual Monday send and the cron's Monday send collide on `_Q_SAME_RUN` and dedupe instead of both going out.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone
from service.campaigns.weekid import week_campaign_id

def _at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)

def test_it_pads_the_week_to_two_digits():
    assert week_campaign_id(_at(2026, 1, 8), 'e2') == 'cmp_2026w02_community'

def test_it_uses_the_iso_week_numbering_year_not_the_calendar_year():
    # 1 January 2027 falls in ISO week 53 of 2026.
    assert week_campaign_id(_at(2027, 1, 1), 'e2') == 'cmp_2026w53_community'

def test_the_whole_monday_to_sunday_week_gets_one_id():
    ids = {week_campaign_id(_at(2026, 9, 21 + n), 'e2') for n in range(7)}
    assert ids == {'cmp_2026w39_community'}

def test_each_campaign_has_its_own_suffix():
    assert week_campaign_id(_at(2026, 9, 21), 'e1') == 'cmp_2026w39_spotlight'
    assert week_campaign_id(_at(2026, 9, 21), 'e3') == 'cmp_2026w39_reinvite'

def test_an_unknown_campaign_falls_back_to_its_own_code():
    assert week_campaign_id(_at(2026, 9, 21), 'e9') == 'cmp_2026w39_e9'
```

- [ ] **Step 2: Run them and watch them fail**

Run: `... /app/tests/run.sh tests/test_campaign_week_id.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'service.campaigns.weekid'`

- [ ] **Step 3: Write the module**

```python
"""The campaign id for a given week.

`ahavah-admin/src/lib/growth-api.ts:565` computes this same string when an
operator presses Send. The cron computes it here. They MUST agree: the
runner's `_Q_SAME_RUN` dedupes on (campaign, campaign_id), so an identical
id is what makes a manual send and a scheduled send in the same week land
as one send rather than two. Change one side and you must change both.
"""
from __future__ import annotations

from datetime import datetime

_SUFFIX = {'e1': 'spotlight', 'e2': 'community', 'e3': 'reinvite'}


def week_campaign_id(now: datetime, campaign: str) -> str:
    # isocalendar() gives the ISO week-numbering year, which is not always
    # the calendar year: 1 January 2027 is 2026 week 53.
    year, week, _ = now.isocalendar()
    return f"cmp_{year}w{week:02d}_{_SUFFIX.get(campaign, campaign)}"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `... /app/tests/run.sh tests/test_campaign_week_id.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add service/campaigns/weekid.py tests/test_campaign_week_id.py
git commit -m "feat(campaigns): week-stamped campaign id shared with the admin frontend"
```

### Task 2: The cron that sends the weekly email

**Files:** Create `service/cron/communityweekly/__init__.py`, modify `service/cron/__init__.py`, create `tests/test_cron_community_weekly.py`.

**Interfaces:**
- Consumes: `week_campaign_id` from Task 1.
- Produces: `community_weekly_forever()`, an async loop registered in `service/cron/__init__.py:main`.

Read `service/cron/emailoutbox/__init__.py` and `service/cron/betareengagement/__init__.py` before writing this. The idiom to copy: a module-level `print(f'Hello from cron module: {__name__}')`, settings through `env_int`, a random start delay, `print_stacktrace(lambda: asyncio.to_thread(...))` so a raising tick never kills the loop, and blocking database work on a thread because `run_campaign` is synchronous.

**The send window, and why it is a window rather than an instant.** The slot is Monday 12:00 UTC, which is 08:00 in Barbados and the same slot `mondaySlots` offers for spotlight cards. A loop polling on an interval will never wake exactly on the hour, and a container restart can skip an instant entirely. So the tick sends when it is Monday, at or after 12:00 UTC, and the week's id has not been used. The id is the real guard; the window only decides when to try.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone
from service.cron.communityweekly import is_send_window

def _at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)

def test_monday_noon_utc_is_in_the_window():
    assert is_send_window(_at(2026, 9, 21, 12, 0)) is True

def test_monday_before_noon_is_not():
    assert is_send_window(_at(2026, 9, 21, 11, 59)) is False

def test_the_rest_of_monday_is_still_in_the_window():
    # The id guard stops a second send, so a late tick must still try.
    assert is_send_window(_at(2026, 9, 21, 23, 59)) is True

def test_no_other_day_is():
    for day in (22, 23, 24, 25, 26, 27):
        assert is_send_window(_at(2026, 9, day, 12, 0)) is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `... /app/tests/run.sh tests/test_cron_community_weekly.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Write the cron module**

```python
"""communityweekly - sends e2, the weekly community email, on a schedule.

Until 2026-09-24 nothing sent this at all. It shipped with a template, a
send script and a Send button, and no scheduler, so it went out exactly
once, by hand, months after it was built.

THE CAMPAIGN ID IS THE GUARD, NOT THE CLOCK.

Every tick inside the window calls run_campaign with the week's id from
`service.campaigns.weekid`. The runner's `_Q_SAME_RUN` rejects a recipient
who already has a row for that (campaign, campaign_id), so:

  * a second tick in the same Monday queues nothing,
  * a container restart mid-Monday queues nothing extra,
  * an operator who presses Send in Growth > Emails the same week collides
    with the identical id and queues nothing extra either.

That last one is why Task 1 exists as a shared module rather than a literal.

The 6-day cap (`CAP_DAYS` in emails/send_community_weekly.py) stays on. It
is a second, independent belt: the id guard protects against a repeat of
THIS run, the cap protects the member from any other campaign that went out
in the last six days.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from database import api_tx
from emails.community_weekly import FROM_ADDR
from emails.send_community_weekly import (
    CAP_DAYS, UNSUB_SCOPE, build_for, recipients)
from service.campaigns.runner import run_campaign
from service.campaigns.weekid import week_campaign_id
from service.config import WEB_BASE_URL
from service.cron.cronutil import env_int, print_stacktrace, MAX_RANDOM_START_DELAY
from service.unsubscribe import make_url as unsub_url

# Monday 12:00 UTC is 08:00 in Barbados, the same slot the member of the
# week cards are scheduled into (`mondaySlots` in the admin frontend).
SEND_WEEKDAY = env_int('DUO_CRON_COMMUNITY_WEEKLY_WEEKDAY', 1)  # 1 = Monday
SEND_HOUR_UTC = env_int('DUO_CRON_COMMUNITY_WEEKLY_HOUR_UTC', 12)

# Hourly. The window is the rest of the day and the id guard absorbs every
# repeat, so the poll only has to be fine enough to catch the day.
COMMUNITY_WEEKLY_POLL_SECONDS = env_int(
    'DUO_CRON_COMMUNITY_WEEKLY_POLL_SECONDS', 60 * 60)

# Off by default so deploying this plan does not send mail on its own. The
# owner turns it on once, deliberately.
COMMUNITY_WEEKLY_ENABLED = env_int('DUO_CRON_COMMUNITY_WEEKLY_ENABLED', 0)

print(f'Hello from cron module: {__name__}')


def is_send_window(now: datetime) -> bool:
    """Monday, at or after the send hour. A window rather than an instant:
    a poll never wakes exactly on the hour and a restart can skip one."""
    return now.isoweekday() == SEND_WEEKDAY and now.hour >= SEND_HOUR_UTC


def _send_once() -> None:
    now = datetime.now(timezone.utc)
    if not is_send_window(now):
        return
    cid = week_campaign_id(now, 'e2')
    out = run_campaign(
        api_tx, 'e2', cid, recipients(), build_for, send=True,
        from_addr=FROM_ADDR, unsub_scope=UNSUB_SCOPE, cap_days=CAP_DAYS,
        list_unsubscribe=lambda e: (
            f"<mailto:support@ahavah.app?subject=Unsubscribe>, "
            f"<{unsub_url(UNSUB_SCOPE, e, WEB_BASE_URL)}>"))
    # A tick that queued nothing is the NORMAL case: every hour of Monday
    # after the first one. Only say something when something happened.
    if out['queued'] or out['error']:
        print(f'community_weekly: {out}')


async def community_weekly_forever() -> None:
    if not COMMUNITY_WEEKLY_ENABLED:
        print('community_weekly: disabled, set DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1')
        return
    await asyncio.sleep(random.randint(0, MAX_RANDOM_START_DELAY))
    while True:
        await print_stacktrace(lambda: asyncio.to_thread(_send_once))
        await asyncio.sleep(COMMUNITY_WEEKLY_POLL_SECONDS)
```

- [ ] **Step 4: Register it**

In `service/cron/__init__.py`, add the import beside the other cron imports and the call inside `main`'s `asyncio.gather`, with a comment in the style of the ones already there:

```python
from service.cron.communityweekly import community_weekly_forever
```

```python
        # e2, the weekly community email. Monday 12:00 UTC, guarded by the
        # week's campaign id. Off unless DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1.
        community_weekly_forever(),
```

- [ ] **Step 5: Run the window tests and watch them pass**

Run: `... /app/tests/run.sh tests/test_cron_community_weekly.py -q`
Expected: 4 passed

- [ ] **Step 6: Prove the id guard with a real second run**

Append to `tests/test_cron_community_weekly.py` a test that calls `run_campaign` twice against the test database with the same week id and asserts the second returns `queued == 0`. Use the existing campaign test fixtures; read `tests/test_campaign_runner.py` for how a recipient list is built there. A test that only asserts the string shape proves nothing about dedupe.

- [ ] **Step 7: Run the whole suite**

Run: `... /app/tests/run.sh tests -q`
Expected: baseline 910 plus the new cases, zero failures.

- [ ] **Step 8: Commit**

```bash
git add service/cron/communityweekly/__init__.py service/cron/__init__.py tests/test_cron_community_weekly.py
git commit -m "feat(cron): send the weekly community email on a schedule"
```

### Task 3: The outbox reports itself

**Files:** Modify `service/admin/queries/system.py`, modify `service/api/admin/system_routes.py`, modify `tests/test_admin_system_health.py`.

**Interfaces:**
- Produces: `/admin/system/health` gains an `outbox` object: `{queued, reserved, accepted_24h, failed_24h, acceptance_unknown, oldest_queued_at}`.

- [ ] **Step 1: Write the failing test**

Extend the existing system health test. Seed `email_outbox` rows in each state, call the route, assert each count. Assert `acceptance_unknown` is reported separately from `failed`, because they mean different things to an operator: failed is known-not-sent, unknown is may-have-been-sent-twice-if-you-retry.

- [ ] **Step 2: Run it and watch it fail**

Run: `... /app/tests/run.sh tests/test_admin_system_health.py -q`
Expected: FAIL, `KeyError: 'outbox'`

- [ ] **Step 3: Add the query**

In `service/admin/queries/system.py`, beside `Q_OTP_24H`:

```python
# Campaign email delivery, which unlike OTP delivery IS measured: every
# message goes through email_outbox and the drain records what happened.
# `acceptance_unknown` is kept apart from `failed` on purpose. Failed means
# known-not-sent and is safe to retry. Unknown means the provider may have
# accepted it before we lost the answer, so a retry may be a second copy in
# a member's inbox.
Q_OUTBOX_HEALTH = """
    SELECT
      count(*) FILTER (WHERE state = 'queued')              AS queued,
      count(*) FILTER (WHERE state = 'reserved')            AS reserved,
      count(*) FILTER (WHERE state = 'acceptance_unknown')  AS acceptance_unknown,
      count(*) FILTER (WHERE state = 'accepted'
                         AND updated_at > NOW() - INTERVAL '24 hours') AS accepted_24h,
      count(*) FILTER (WHERE state = 'failed'
                         AND updated_at > NOW() - INTERVAL '24 hours') AS failed_24h,
      min(created_at) FILTER (WHERE state = 'queued')       AS oldest_queued_at
      FROM email_outbox
"""
```

Confirm the column names against `migrations/` before writing: the table uses `state`, not `status`, and the reviewer should check whether the timestamp column is `updated_at` or another name rather than trusting this draft.

- [ ] **Step 4: Add it to the route**

In `service/api/admin/system_routes.py`, execute it inside the existing `api_tx` block and add `'outbox': dict(outbox)` to the returned dict. Do not open a second transaction: the api connection lock is not reentrant.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `... /app/tests/run.sh tests/test_admin_system_health.py -q`

- [ ] **Step 6: Commit**

```bash
git add service/admin/queries/system.py service/api/admin/system_routes.py tests/test_admin_system_health.py
git commit -m "feat(admin): report real campaign email delivery on system health"
```

### Task 4: The System tab stops claiming a number it does not have

**Files:** Modify `ahavah-admin/src/components/admin/tab-system.tsx`, modify `ahavah-admin/src/lib/queries.ts` (the `SystemHealth` type), modify the matching admin test.

The OTP panel currently shows a 44px green `100%` and a literal `0` for failures. `Q_OTP_24H` counts `duo_session` rows, so what is actually known is how many codes were issued. Nothing measures whether they arrived.

- [ ] **Step 1: Delete the two literals**

- Remove the `"100%"` expression at `tab-system.tsx:97` and the `0` at `tab-system.tsx:113` with the "Failures" label beneath it.
- Retitle the card from "OTP deliverability" to "OTP codes", which is what it measures, and keep `sent_24h` as the one number it shows, labelled "issued · 24h".
- Replace the deferral note with one plain sentence: "Whether codes arrive is not measured. Delivery tracking needs the provider's logs."

- [ ] **Step 2: Add the email delivery card**

A sibling `AdminCard` titled "Email delivery", built only from the primitives already in `design-primitives.tsx` (`AdminCard`, `SectionLabel`, `Badge`) so it matches the cards either side of it. It shows `accepted_24h` as the headline number with "accepted · 24h" beneath, then `failed_24h` and `acceptance_unknown` as the two secondary figures, and `queued` with the age of `oldest_queued_at` when anything is waiting. A non-zero `acceptance_unknown` gets the same warning treatment `growth-emails.tsx` already uses for it, with the copy: "may have been delivered. Check before resending."

Every number comes from the route. No literal stands in for a measurement anywhere in this card.

- [ ] **Step 3: Update the type and the test**

Add `outbox` to the `SystemHealth` type in `queries.ts`. Update the component test to assert the card renders the route's numbers, and add a case asserting that nothing renders a success percentage. `npx tsc --noEmit` must pass: a mocked state missing a new field has broken this project's build while the tests stayed green.

- [ ] **Step 4: Run and commit**

```bash
npm test && npx tsc --noEmit
git add src/components/admin/tab-system.tsx src/lib/queries.ts src/components/admin/__tests__/tab-system.test.tsx
git commit -m "fix(admin): stop reporting an OTP success rate nothing measures"
```

### Task 5: The two sentences that point at nothing

**Files:** Modify `ahavah-admin/src/components/admin/growth-emails.tsx`, modify `ahavah-admin/src/lib/growth-queries.ts`.

- [ ] Once Task 4 ships, "Check the outbox before sending again" has somewhere to point. Change it to name the place: "Check Email delivery on the System tab before sending again."
- [ ] Fix the stale comment at `growth-queries.ts:87` the same way.
- [ ] Add the next send line to the e2 row: when the cron is enabled, "Next send Monday 12:00 UTC"; when it is not, "Not scheduled". A last-sent date with no next-send date is what let this go unnoticed, so the row has to say which of the two it is.
- [ ] Commit.

### Task 6: Review, deploy, and turn it on deliberately

- [ ] Whole branch review; one fix wave if needed.
- [ ] Deploy the API with `DUO_CRON_COMMUNITY_WEEKLY_ENABLED` unset, so the schedule ships dormant. Confirm the cron logs its disabled line.
- [ ] Deploy the admin. Confirm the System tab shows the real outbox numbers from the 24 September run and no percentage anywhere.
- [ ] On the owner's go, set `DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1` and restart the cron.
- [ ] **Watch the first scheduled Monday.** Confirm exactly one `community_weekly:` line with a non-zero `queued`, confirm `email_send_log` gains one e2 group with `campaign_id = cmp_2026wNN_community`, and confirm no member has two rows for it.

## Self-review record

- **The riskiest thing here is a double send**, so the guard is the runner's existing per-run dedupe rather than anything new, and it is shared with the frontend so a manual send in the same week collides with it instead of racing it. The 6-day cap stays on underneath as an independent second guard.
- **The schedule ships off.** A deploy that starts mailing 26 people on its own is not something the owner asked for, and the cost of one explicit environment variable is one line.
- **Task 4 is the task that makes Task 3 worth doing**, and Task 5 is only honest once Task 4 exists. They ship together.
- Reuse over invention: the existing cron registry, the existing runner, the existing outbox, the existing health route, the existing design primitives. The only genuinely new file is the cron module and the five-line id helper.
- **Open question for the owner, flagged rather than decided:** house rule is that UI comes from a Claude Design brief. Task 4's card is a composition of primitives already approved and already used on that tab, not a new design, so my recommendation is an explicit waiver for it. If you would rather it go through a brief, Tasks 1 to 3 and 5 still stand on their own and Task 4 waits. **Resolved 2026-09-24: the owner waived the brief for this card.**

---

## Review record, 2026-09-24

A whole-branch adversarial review found **two Critical defects, both in work this plan called done.** Recorded here rather than quietly fixed, because both were failures of verification rather than of implementation, and the same shape of mistake is easy to repeat.

**Critical 1. The two campaign id implementations never agreed.** The admin frontend's `CAMPAIGN_SUFFIX` is `{e1: "spotlight", e2: "comm", e3: "reinv"}`. Task 1 shipped `{'e1': 'spotlight', 'e2': 'community', 'e3': 'reinvite'}`. Only e1 matched. The one campaign this plan schedules did not, so the entire double send guard, which every docstring and commit message on this branch asserts, would never have fired. What would actually have stood between a member and two copies is the 6-day frequency cap: the guard this plan explicitly designates as the backup, and the one an operator overrides when they want a send to go out.

The suffix table was written from a reading of `campaignId()` without reading the table it indexes into. Task 1 then wrote a test named `test_the_cron_and_the_admin_frontend_agree_on_the_id` whose body compared the Python function to its own output, under a docstring claiming it checked the admin repo. It passed for as long as the two sides disagreed.

Fixed by taking the admin's values (they are the established ones, already in `email_send_log`) and by moving the real check to `ahavah-admin/tests/growth-api.test.mjs`, which reads `weekid.py` off disk and compares suffix by suffix. That suite runs on the host under `node --test`, so a genuine cross-repo read is available there and is not available inside the API's test container. Proven by reverting the suffix and watching it fail with the right message.

**Critical 2. None of the new environment variables reached any container.** `docker-compose.production.yml` deliberately uses explicit `${VAR}` substitution with no `env_file`, so a variable absent from a service block does not exist for that service. All four were absent from the compose file and from `.env.production.template`. Task 6's "set `DUO_CRON_COMMUNITY_WEEKLY_ENABLED=1` and restart" would have produced the same reassuring `community_weekly: disabled` line as the dormant deploy in the step before it, and the schedule would never have run. That is the original defect, reproduced.

Fixed by adding all four to the cron service, the three shared ones to the api service, and the set to the template with a note on why both services need them.

**Also fixed from the same review:**

- `Q_OUTBOX_HEALTH` counted five of the outbox's six states, dropping `skipped`, while the card's own copy claimed every message was accounted for. `skipped` is where the drain puts anything the frequency cap refuses, so it is precisely the bucket the new schedule will fill. Now a column, a stat, and a test that walks `outbox.STATES`.
- One figure was windowed and four were all-time totals on a table nothing prunes, presented as five adjacent unlabelled numbers. A single stuck row would have put a permanent red warning on the System tab. Now labelled "· all time", and the warning says it does not clear on its own.
- `python -m emails.send_community_weekly --send` minted `e2-<random hex>`, which can collide with nothing. An operator running it on a Monday afternoon after the cron had already sent would have queued a second full cohort. It now computes the same week id as everything else.
- `oldest_queued_at` went out as an RFC 2822 HTTP-date, because the route handed Flask a raw `datetime` while the TypeScript reading it is typed and tested against ISO 8601. The test that should have caught it asserted that a string appeared in the route's source.
- Two API tests asserted on source text where the `client` fixture was right there. Replaced with real requests through the Flask client, including an admin-gate case. The source assertions in the ADMIN suite stay: that suite has no DOM and no renderer, and the rest of the directory tests UI the same way. They now match on collapsed whitespace so JSX wrapping cannot break them.
- `DUO_CRON_COMMUNITY_WEEKLY_ENABLED=true` silently read as off. Unrecognised values now raise at import instead, as do an out-of-range weekday or hour, which would otherwise have taken the whole Emails tab down with a 500 from inside `datetime.replace(hour=25)`.
- A poll interval longer than the send window would have stepped over Mondays in silence. Refused at import.
- **A missed week was invisible**, which is the exact mirror of the defect this plan exists to fix: the row would have gone on printing "Next Monday, 08:00" while the last-sent date quietly stopped advancing. The schedule payload now carries `overdue`, and the row says "last week was missed".
- The dedupe test took the `outbox_drain` fixture and never called it, so what it proved was the outbox's unique key, not the `_Q_SAME_RUN` guard named everywhere in this branch. Renamed to say what it actually covers, and a real `_Q_SAME_RUN` test added that drains first and disables the cap so nothing else can be doing the work.

**Found and fixed outside the plan's scope, flagged rather than folded in silently:** the System tab carried a third card, "Mail", printing `otp.sent_24h` under the claim it counted "OTP, signup confirmations, beta-launch invites, and referral nudges". It counted none of those. Same defect class as the hardcoded 100%, in the same file, found while fixing it. Deleted. Relatedly, `Q_OTP_24H` counts sessions by `otp_expiry`, which is bumped per code and zeroed on sign-in, so three codes on one session count once; the card had been relabelled "codes issued", which was still wrong. It now says sessions and explains.

**Known and deliberately not fixed here:**

- `email_outbox` has no retention. Nothing deletes a row, so the all-time counts grow forever and `Q_OUTBOX_HEALTH` is an unindexed scan under a 5s statement timeout. Fine at 26 messages a week; it needs a retention job before it is not. Separate plan.
- A missed week is now visible on the dashboard but still raises no alert.
- Windowing failures properly needs a failure timestamp on the table, which is a migration.

**Plan errors, for the next person reading it:** Task 3's draft query used an `updated_at` column that does not exist on `email_outbox` (corrected during implementation, before review). Task 4's verification step said `npm test`; `ahavah-admin/package.json` has no test script and the suite runs as `node --test "tests/*.test.mjs"`.
