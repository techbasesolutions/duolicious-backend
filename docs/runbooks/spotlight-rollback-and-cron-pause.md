# Community Spotlight: rollback and cron pause

An operator's runbook for stopping Community Spotlight quickly and rolling
back a bad deploy, in the order that actually stops harm first. Every step
names the exact command or admin click and how to confirm it worked. Repos:
`ahavah-api` (deploys via GitHub Actions to the droplet on push to
`ahavah/main`), `ahavah-web` and `ahavah-admin` (deploy via Vercel, team
`techbase-hq`, on push to `master`).

## 1. Stop first: emergency stop, then invites off

Do these in this order. The emergency stop blocks every outbound call to
Meta immediately, which is the highest-harm surface (a bad post or a bad
deletion). Invites off stops new members from being drawn into the pipeline
while the rest is being worked.

1. **Emergency stop.** Growth tab -> Controls -> the "External access" switch,
   turn it off. This is the emergency stop by name in the code and the UI:
   with it off, nothing is sent to Meta, no posts and no deletions
   (`ahavah-admin/src/components/admin/growth-controls.tsx`, key
   `external_access_enabled`). Equivalent API call, with an admin session
   cookie:

   ```
   POST /admin/growth/settings
   {"key": "external_access_enabled", "value": "false"}
   ```

   Confirm: the switch reads off and red in the Growth tab, or the response
   is `{"ok": true, "key": "external_access_enabled", "value": "false"}`.
   `GET /admin/growth/queue/claim` (or the next `publish-due` cron run) then
   reports `"halted": true`; `GET /admin/growth/removals` reports the same
   under `halted` and still returns the `overdue` and `outstanding_cleanup`
   counts, which stay visible on purpose (`service/api/admin/spotlight_routes.py`,
   `get_growth_removals`).

2. **Invites off.** Growth tab -> Controls -> the "Invites" switch, turn it
   off (key `invites_enabled`). Equivalent API call:

   ```
   POST /admin/growth/settings
   {"key": "invites_enabled", "value": "false"}
   ```

   Confirm: the switch reads off, or the response is `{"ok": true, "key":
   "invites_enabled", "value": "false"}`. The next daily tick then creates no
   new welcome or roundup candidates and sends no new E4 (card-ready) email
   (`ahavah-admin/src/lib/tick.ts`, `result.invites_paused`).

   **Caveat, confirmed against the local acceptance run
   (`docs/superpowers/plans/2026-09-16-spotlight-acceptance-local.md`,
   section 6):** `invites_enabled` only gates the creation of new candidates
   and their E4. It does not stop `publication_enabled` or
   `external_access_enabled` from being independent controls, and it does
   not stop an E4 that was already queued in the email outbox before the
   switch was flipped: the outbox drain does not read `invites_enabled`, so
   that mail still sends while invites are off. Nothing in the in-app
   controls, and nothing in pausing the Vercel admin crons (section 3
   below), stops that drain either: it is a separate loop on the API's own
   droplet, unrelated to either. If a card-ready email, or any other queued
   campaign mail, must not go out at all, section 2 below is what actually
   stops it.

   `publication_enabled` (the ordinary pause, "nothing is claimed for
   posting; rendering, approvals and removals continue") and
   `approvals_enabled` / `roundup_tiles_enabled` (read-only chips, set from
   the API only) are the other three controls in the Growth tab; they are
   not part of the stop sequence above because the emergency stop already
   covers everything they would otherwise limit.

## 2. Stop mail actually sending: the API's own outbox drain

Section 1's controls and section 3's Vercel cron pause stop the admin app
from creating new candidates, claiming rows for posting, and answering its
own cron routes at all. Neither one touches the process that actually sends
mail. `email_outbox_forever` in `service/cron/emailoutbox/__init__.py` runs
inside the API's own `cron` Docker container on the droplet, polling every
`DUO_CRON_EMAIL_OUTBOX_POLL_SECONDS` (default 30 seconds,
`docker-compose.production.yml`) and calling `service/campaigns/outbox.drain`,
"the only place SMTP is spoken anywhere in the system" per its own docstring.
It reads neither `invites_enabled` nor `external_access_enabled`, and it has
nothing to do with the three Vercel crons in section 3. If a queued E4, E5,
or any other campaign mail must not go out, this container has to be stopped
directly.

On the droplet, in `/opt/ahavah-api` (the deploy workflow's own working
directory):

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production stop cron
```

Confirm it stopped: `docker compose -f docker-compose.yml -f
docker-compose.production.yml --env-file .env.production ps cron` shows no
running container. Confirm nothing is still sending, read-only, safe to run
repeatedly (`email_outbox.sent_at` is stamped only on a successful send,
`service/campaigns/outbox.py`):

```sql
SELECT count(*) AS sent_since_stop FROM email_outbox WHERE sent_at > '<time you ran the stop command, UTC>';
```

Run it again a few minutes later; it should still read 0. A single row
appearing shortly after the stop command usually means one send was already
in flight (the drain calls SMTP synchronously) when `stop`'s grace period
began, not that the stop failed; a count that keeps growing means it did.

**This is a blunt instrument.** The `cron` container runs one `asyncio.gather`
of everything below (`service/cron/__init__.py`); stopping the whole
container to silence mail pauses all of it, so an operator needs to know
what else goes quiet and for how long that is acceptable:

- `email_outbox_forever` -- the target of this step.
- `spotlight_cleanup_forever`, `spotlight_retention_forever` -- Spotlight's
  own cleanup and retention sweeps. Safe to pause for the length of an
  incident; work queues and drains once resumed. Do not leave it down long
  enough for a removal task's 72-hour deadline
  (`spotlight_removal_task.deadline_at`, migration 0046) to pass unwatched,
  since the overdue count this pause otherwise keeps visible stops updating
  too.
- `autodeactivate2_forever`, `delete_garbage_records_forever`,
  `clean_photos_forever`, `clean_audio_forever`, `report_profiles_forever`,
  `send_beta_reengagement_forever` -- routine housekeeping and reporting.
  Safe to pause for hours; nothing time-critical.
- `entitlements_forever` -- strips an expired premium entitlement, hourly.
  Pausing lets an already-expired subscriber keep premium access a little
  longer than they should. Low stakes for a short pause.
- `hard_delete_expired_forever` -- hard-deletes accounts past their 7-day
  pending-deletion grace window, hourly. Pausing delays those deletions;
  keep this outage short, since this is the step that actually honours a
  member's deletion request.
- `predict_nsfw_photos_forever` -- moderates newly uploaded photos. Pausing
  this stops NSFW screening on anything uploaded while the container is
  down. Treat this as the strongest reason to keep the outage as short as
  possible, not something to leave paused casually.
- `build_firehol_forever` -- the single writer of the FireHOL IP blocklist
  the API workers mmap, every 4 hours. Pausing beyond a few hours lets the
  blocklist go stale (newly published malicious ranges are not picked up);
  IPs already in the last-written blocklist stay blocked regardless.
- `send_notifications_forever`, `verify_forever` -- push notifications and
  identity/photo verification processing. Pausing delays both; they queue
  and catch up once resumed.
- `check_connections_forever`, `http_server` -- an internal connection
  health check and the container's own `/health` endpoint on port 8080. No
  user-facing effect from pausing either.

Resume:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production up -d cron
```

Confirm: `docker compose ... ps cron` shows it running, and `docker logs
--tail 20 <cron container>` shows no traceback.

## 3. Stop the Vercel crons (admin app)

Two ways. Pick one depending on whether you need it back quickly or need it
to stay off through unrelated deploys.

**Option A: unset `CRON_SECRET` in the Vercel admin project (faster).**
In the Vercel dashboard, `techbase-hq` team, the `ahavah-admin` project,
Settings -> Environment Variables: delete or blank the `CRON_SECRET` value
for Production, then trigger a redeploy of the current production
deployment from the dashboard (Deployments -> the current one -> Redeploy).
No code change and no git push. `cronAuthorised` in
`ahavah-admin/src/lib/publishing.ts` returns `false` when `CRON_SECRET` is
unset, so every one of the three cron routes (`/api/growth/publish-due`,
`/api/growth/token-health`, `/api/growth/tick`) answers 401 regardless of
who calls it, Vercel Cron included. This is faster because it needs no git
commit, only a dashboard edit and a redeploy. It does **not** survive
unrelated work: the crons resume the moment anyone sets `CRON_SECRET` back
to a real value and redeploys (including a routine, unrelated redeploy that
happens to restore an environment variable from a template or a promoted
preview).

**Option B: remove the cron entries from `vercel.json` and push (survives
a redeploy).** Comment out or delete the three entries in
`ahavah-admin/vercel.json`:

```json
{
  "crons": []
}
```

Commit and push to `master`. Vercel registers cron schedules from
`vercel.json` at deploy time, so once this is live, no cron job is
registered at all, not merely refused. This is slower (a real deploy) but
durable: any later push to `master` that does not re-add the entries keeps
the crons off, because the deployed config is the source of truth rather
than a value someone could restore from the dashboard.

Confirm either way: in Vercel, Project -> Cron Jobs, the three entries show
no next scheduled run (Option B) or a next run that returns 401 in the
function logs (Option A). `GET /api/growth/publish-due` (or the other two)
called directly, with no `Authorization` header, answers `401 {"error": "Not
authorised."}` in both cases.

The "faster" versus "survives a redeploy" comparison above is reasoned from
Vercel's documented behaviour for `vercel.json` cron registration and
environment variable changes, not from timing either option on this
project's own Vercel account.

## 4. Roll back each deploy

**API (the droplet).** A rollback is re-deploying a previous commit. Either:

- Push the previous good commit to `ahavah/main` (a revert commit, or
  force-pushing history back, per the team's normal git policy) and let
  `.github/workflows/deploy-ahavah.yml` run as usual: it SSHes to the
  droplet (see the deploy workflow's host secret), resets `/opt/ahavah-api`
  to `origin/ahavah/main`, rebuilds, applies `scripts/apply-deploy-migrations.sh`
  (a no-op if nothing new needs applying), then runs
  `docker compose -f docker-compose.yml -f docker-compose.production.yml
  --env-file .env.production up -d --force-recreate api chat cron`.
- Or, directly on the droplet: `cd /opt/ahavah-api && git checkout <previous-sha>`,
  then run the same compose command by hand:
  `docker compose -f docker-compose.yml -f docker-compose.production.yml
  --env-file .env.production build api chat cron && docker compose -f
  docker-compose.yml -f docker-compose.production.yml --env-file
  .env.production up -d --force-recreate api chat cron`.

  Confirm: `git log --oneline -1` on the droplet shows the target sha; the
  workflow's own health check (`curl -fsS http://localhost:5000/health`)
  or the same command run by hand returns healthy; `docker compose ps`
  shows `api`, `chat` and `cron` all `running` with `RestartCount` at 0.

**Web (ahavah.app) and admin (admin.ahavah.app).** Vercel dashboard,
`techbase-hq` team, the relevant project, Deployments: find the previous
production deployment and choose "Instant Rollback". Confirm: the
Deployments list shows that deployment promoted back to Production, and the
live site or admin app reflects the older build (check a page or string that
changed in the bad deploy).

## 5. What cannot be rolled back

- **Forward-only migrations.** Every Spotlight migration from 0046 to 0051
  is additive; rolling the API's code back to a commit before any of these
  does not require rolling the schema back, because the old code simply
  never reads the new tables, columns or indexes:
  - `0046_spotlight_wave2.sql` -- adds the `email_outbox` and `cleanup_job`
    tables outright, adds `attempts`, `next_attempt_at`, `deadline_at`,
    `last_error` and `evidence` columns to `spotlight_removal_task`
    (backfilling `deadline_at` on every existing row), and adds
    `image_sha256` to `publishing_queue`. Old code never queries the new
    tables and never reads any of the new columns. Runs cleanly.
  - `0047_cleanup_job_partial_unique.sql` -- replaces an unconditional
    unique constraint on `cleanup_job (kind, target)` with a partial one
    scoped to `state = 'pending'`. Old code's `INSERT ... ON CONFLICT` still
    matches the narrower index. Runs cleanly.
  - `0048_click_receipts.sql` -- adds nullable `receipt`, `platform`,
    `consumed_at` to `campaign_click`, plus a partial unique index on
    `receipt`. Old (pre-attribution) code that never sets these columns
    leaves them null and is unaffected. Runs cleanly.
  - `0049_cleanup_job_updated_at.sql` -- adds `updated_at timestamptz NOT
    NULL DEFAULT NOW()`, backfilled in the same statement. Old code that
    never sets it just doesn't touch it. Runs cleanly.
  - `0050_spotlight_welcome_unique.sql` -- adds a unique index enforcing at
    most one live welcome per member and platform. Old code that predates
    the matching `except psycopg.errors.UniqueViolation` handler in
    `service/api/admin/spotlight_routes.py` (the code this migration was
    written to support) does **not** catch the resulting conflict: a race
    that used to silently create duplicate welcome rows now raises an
    unhandled integrity error on the second, concurrent insert instead of
    the newer code's clean `409`. This is the one migration on this list
    where "runs cleanly" needs a caveat: the schema change is compatible,
    but a code rollback to before the corresponding application fix can
    surface a 500 on a race that previously succeeded quietly. It does not
    corrupt data or block deploys either way.
  - `0051` (render attempt tracking on `publishing_queue`, if present at
    rollback time) -- adds `render_attempts` and `render_next_attempt_at`
    columns. Additive, nullable/defaulted. Old code never reads them. Runs
    cleanly.

  In short: rolling the API code back while the database stays on the newer
  schema is safe for every migration in this range except the narrow race
  described under 0050, which trades a clean 409 for an occasional 500 and
  nothing worse.

- **Posts already live.** A code or config rollback does not un-post
  anything already on Facebook or Instagram. Facebook posts are removed by
  the automated worker (`processRemovals` in `ahavah-admin/src/lib/publishing.ts`)
  once a removal task exists for them, as long as the emergency stop
  (section 1) is off -- while the emergency stop is on, no removal worker
  call reaches Graph at all, so a live Facebook post stays live until the
  stop is lifted or an operator deletes it by hand on Facebook. Instagram
  has no delete API; those posts, and any row the system could not resolve
  automatically, land in the Growth tab's "Remove by hand" section
  (`ahavah-admin/src/components/admin/growth-removals.tsx`). An operator
  opens the live post on the platform, deletes it there, then ticks the row
  in "Remove by hand", which calls `POST /admin/growth/removals/<id>/done`.

- **Mail already accepted by the provider.** Once Resend (the SMTP/API
  provider behind `service/mail`) has accepted a message, it cannot be
  recalled from a recipient's inbox by anything in this codebase. Turning
  off `invites_enabled`, the emergency stop, or pausing the Vercel crons
  (section 3) stops future sends but, per the caveat in section 1, does not
  stop mail already queued in `email_outbox` from draining; only stopping
  the droplet's `cron` container (section 2) does that, and even then any
  send already handed to Resend before the container stopped is gone.

## 6. How to resume

Reverse order, with a check before each step:

1. **Confirm the deploy is healthy** before touching anything else: the
   API's `/health` endpoint is up, `docker compose ps` shows `api` and
   `chat` running with no recent restarts, and (if admin or web were rolled
   back) the Vercel deployment you promoted is serving traffic without
   elevated error rates in its function logs.

2. **Restart the droplet's `cron` container** (section 2), as soon as step 1
   is confirmed, before touching any Spotlight-specific control: it also
   runs NSFW photo moderation, push notifications, identity verification,
   the FireHOL blocklist writer and the GDPR pending-deletion hard-delete,
   none of which should stay paused any longer than the incident actually
   required. `docker compose -f docker-compose.yml -f
   docker-compose.production.yml --env-file .env.production up -d cron`.
   Confirm: `docker compose ... ps cron` shows it running with no recent
   restarts, `docker logs --tail 20 <cron container>` shows no traceback,
   and if mail was queued during the outage, the `sent_since_stop` query
   from section 2 starts returning a growing count again.

3. **Re-enable the Vercel crons** (section 3), whichever option was used:
   put `CRON_SECRET` back in Vercel and redeploy, or restore the three
   entries in `ahavah-admin/vercel.json` and push. Confirm: Vercel's Cron
   Jobs page shows the three schedules active again, and a manual call to
   `/api/growth/publish-due` with the correct bearer token answers 200.

4. **Turn `publication_enabled` and `external_access_enabled` back on**
   (Growth tab -> Controls, or the same `POST /admin/growth/settings` calls
   as section 1 with `"value": "true"`) only after steps 1 to 3 are
   confirmed. Confirm: `GET /admin/growth/queue/claim` no longer reports
   `halted` or `paused`; the next `publish-due` cron run shows real claims
   rather than an empty list.

5. **Turn `invites_enabled` back on last.** Before flipping it, check the
   Growth tab's queue for anything that piled up while invites were paused
   (an ageing `invites_pending_oldest_days` on `GET /admin/growth/candidates`
   is the signal), and check the removal list is not carrying an unexpected
   backlog from the incident. Confirm: the switch reads on, and the next
   tick resumes creating welcome and roundup candidates
   (`welcomes_created` and `roundup_created` in the tick's own result move
   again instead of staying at zero).
