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
   `external_access_enabled`). Equivalent API call, with an admin's session
   token as `Authorization: Bearer <token>` (the API reads the bearer
   header, not a cookie):

   ```
   POST /admin/growth/settings
   {"key": "external_access_enabled", "value": "false"}
   ```

   Confirm: the switch reads off and red in the Growth tab, or the response
   is `{"ok": true, "key": "external_access_enabled", "value": "false"}`.
   Then read it back, read-only:

   - `GET /admin/growth/settings`, same bearer header. It answers one flat
     object of the stored controls, every value a string, for example
     `{"approvals_enabled": "false", "external_access_enabled": "false",
     "invites_enabled": "true", "publication_enabled": "true", ...}` (only
     the five controls plus `token_expires_at` and `token_valid`;
     `get_growth_settings` in `service/api/admin/spotlight_routes.py`).
     Anything other than `"true"` counts as off: the claim and removals
     routes both test `!= 'true'`.
   - The worker's own view: `GET /api/growth/publish-due?dry=1` on the admin
     app, with `Authorization: Bearer <CRON_SECRET>`. A dry run reads the
     settings and lists due rows; it never claims anything and never calls
     Meta (`publishDue` and `processRemovals` in
     `ahavah-admin/src/lib/publishing.ts`). It answers
     `{"publish": {"claimed": 0, ..., "dry": true, "paused": <bool>,
     "halted": true, ...}, "removals": {..., "halted": true}}`. `claimed` is
     a count, not a list. With `CRON_SECRET` unset (section 3, option A) it
     answers 401 instead, and it answers 503 with an `error` if the admin app
     cannot read the API. A scheduled (not dry) run under the stop returns
     the same shape with `"dry": false`, `publish.claimed` 0 and
     `publish.halted` true.
   - `GET /admin/growth/removals` reports `"halted": true` and still returns
     the `overdue` and `outstanding_cleanup` counts, which stay visible on
     purpose (`get_growth_removals`).

   **Never call `POST /admin/growth/queue/claim` by hand, not even to check
   the flags.** It is POST only and it is not a read. With both controls on
   it runs `claim_spotlight_posts`, which moves up to 2 real rows (the route's
   default `max`) to `status = 'processing'` with `delivery_state =
   'attempting'` and a ten minute lease (`migrations/0044_spotlight_wave1.sql`).
   Nothing will publish them, so when the lease runs out
   `reap_expired_leases` (`service/spotlight/queue.py`) parks them in
   `review` with `error = 'lease_expired'`, indistinguishable from a publish
   that may have reached the platform, and a human then has to check each
   one by hand. Even under the stop it still reaps expired leases and writes
   an audit row.

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
- `autodeactivate2_forever`, `clean_photos_forever`, `clean_audio_forever`,
  `send_beta_reengagement_forever` -- routine housekeeping and reporting
  (re-read: autodeactivation email, unused-photo and unused-audio object
  store cleanup, beta re-engagement email; none of the three touches
  moderation or safety). Safe to pause for hours; nothing time-critical.
- `entitlements_forever` -- strips an expired premium entitlement, hourly.
  Pausing lets an already-expired subscriber keep premium access a little
  longer than they should. Low stakes for a short pause.
- `hard_delete_expired_forever` -- hard-deletes accounts past their 7-day
  pending-deletion grace window, hourly. Pausing delays those deletions;
  keep this outage short, since this is the step that actually honours a
  member's deletion request.
- `predict_nsfw_photos_forever` -- scores newly uploaded photos for NSFW
  content (`antiabuse.antiporn.predict_nsfw`, writing `nsfw_score`). Pausing
  this stops NSFW screening on anything uploaded while the container is
  down.
- `delete_garbage_records_forever` -- not routine housekeeping: its query
  (`Q_DELETE_GARBAGE_RECORDS`) is what actually acts on that score, hard-
  deleting any photo with `nsfw_score > 0.8` and emailing the admin inbox a
  false-positive review list (`service/cron/garbagerecords/__init__.py`,
  `_send_nsfw_admin_notice`). Pausing it, together with the scoring job
  above, means a newly uploaded NSFW photo is neither scored nor removed for
  as long as the container is down. Treat these two jobs together as the
  strongest reason to keep the outage as short as possible, not something to
  leave paused casually.
- `report_profiles_forever` -- not routine reporting either: it scans
  unmoderated profile text with `antiabuse.childsafety.potential_minor` and
  automatically lodges a child-safety report on anything it flags
  (`service/cron/profilereporter/__init__.py`, using
  `antiabuse.lodgereport.skip_by_uuid`). Pausing it suspends automated
  minor-detection reporting on new and unmoderated profiles for as long as
  the container is down. Same standard as the NSFW pair above: keep the
  outage short.
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

### The three rollbacks are coupled since Wave 3d

Do not treat the API, web and admin rollbacks as independent. The production
heads before Wave 3d, which are the rollback targets, are:

| Repo | Rollback target | How |
| --- | --- | --- |
| `ahavah-api` | `5500740` | push to `ahavah/main` or check out on the droplet, as above |
| `ahavah-web` | `e452c8a` | Instant Rollback to the production deployment built from that commit |
| `ahavah-admin` | `30daef1` | Instant Rollback to the production deployment built from that commit |

What breaks when only part of the set is rolled back:

| What is rolled back | What happens | Why (verified in code) |
| --- | --- | --- |
| Web alone (API stays new) | With approvals on, every member "approve" answers 400 and no consent is recorded. "Skip" still works. With approvals off no approval lands either way (409 `approvals_disabled`, or a 400 on an API head where the approvals check still runs after the revision check). | The old card page posts `{decision, photo_uuid}` with no `revision` (`ahavah-web` at `e452c8a`, `src/app/spotlight/card/[token]/page.tsx`). The new `post_spotlight_card` (`service/api/spotlight_card_routes.py`) aborts 400 unless `revision` is an integer. |
| API alone, admin stays new | No card renders. Every upload from the tick is refused 400 `invalid_image`, and the tick's follow-up render-failed report gets a 404 that the tick swallows, so the card is simply listed again next tick and fails again. | The API at `5500740` reads only `png_base64`; the new admin sends `image_base64` plus `content_type: 'image/jpeg'` (`queueImage` in `ahavah-admin/src/lib/growth-server.ts`). The old route decodes an empty string and `validate_png` refuses it. `POST /admin/growth/queue/<key>/render-failed` does not exist before Wave 3d; `runTick` in `ahavah-admin/src/lib/tick.ts` catches that failure and carries on. |
| API alone, admin stays new (removals) | The removals worker's `worker=1` filter is ignored, so its listing is again the newest 200 pending tasks of every reason, and a backlog of `manual_instagram` or `investigate` tasks can hide the `delete_via_api` tasks it can act on. | `worker` is read only by the new `get_growth_removals` and `_Q_REMOVALS`; the old query orders by `created_at DESC`, limit 200. |
| API to any commit before `52763e8` (which includes `5500740`) | Every operator approve of a rendered card answers 503 `storage_unavailable` and the row goes back to `review`. Nothing can be scheduled. | Before `52763e8`, `make_public` in `service/spotlight/storage.py` calls `put_object_acl` on an `s3.Object` resource, which boto3 1.35.99 does not have; `post_growth_queue_approve` turns that into the 503 (`docs/superpowers/plans/2026-09-16-spotlight-acceptance-local.md`, Storage 5a). |
| API alone, web stays new | Silent: nothing errors. A member approving from a stale tab has consent recorded against the current revision, a card they may not have seen. | The old `post_spotlight_card` never reads `revision`, and the old `approve_card` (`service/spotlight/revisions.py`) has no `shown_revision` check. |
| Admin alone (API stays new) | Cards still render, but as PNG: the new API still accepts `png_base64`. The old publisher posts Facebook photos with `message`, defaults to Graph v21.0, and treats a lost `image_race` as a render failure. | `queueImage` and `publishRow` in `ahavah-admin` at `30daef1`. These are the reasons the Wave 3d plan keeps `CRON_SECRET` and `AHAVAH_GROWTH_CRON_SECRET` unset until the new admin is live; the plan records that Instagram publishes JPEG only (`docs/superpowers/plans/2026-09-16-spotlight-wave-3d.md`, Task 6 and the deploy conditions). |

Rules:

1. **Before any API rollback, turn `approvals_enabled` and
   `publication_enabled` off** (`POST /admin/growth/settings`, as in section
   1; `approvals_enabled` is one of the accepted keys even though the Growth
   tab only shows it as a chip) and confirm both read `"false"` on
   `GET /admin/growth/settings`. Keep them off for as long as any repo is on
   a different side of Wave 3d from the others.
2. **The API and admin roll back together**, to `5500740` and `30daef1`. Stop
   the Vercel crons first (section 3) so no tick runs between the two, and
   keep them stopped while the admin is on `30daef1`, per the Wave 3d deploy
   conditions (section 6, step 4).
3. **Web never rolls back without the API while approvals are on.** With
   approvals off, web at `e452c8a` against the new API refuses approvals but
   harms nothing, and web left new against the old API is harmless too.
4. Rolling the API back to `5500740` needs no schema change: the only
   migration files added since are `0050` and `0051` (`git diff --stat
   5500740 <wave 3d head> -- migrations/` lists nothing else), so
   `scripts/apply-deploy-migrations.sh` finds no changed checksum and never
   looks at the two applied files the old checkout does not carry. See
   section 5 for what the old code does on the new schema.
5. Going forward again, deploy in the Wave 3d plan's order: API, then web,
   then admin.

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
  - `0050_spotlight_welcome_unique.sql` -- one transaction: `BEGIN`;
    `LOCK TABLE publishing_queue IN SHARE MODE` (reads continue, every
    insert, update and delete waits); a `DO` block that raises, naming each
    member and platform, if any `subject_person_id` holds more than one
    welcome with `status <> 'cancelled'` on the same platform (rows with a
    NULL `subject_person_id`, a deleted member's, are excluded from the
    check); `CREATE UNIQUE INDEX IF NOT EXISTS
    publishing_queue_one_live_welcome ON publishing_queue (subject_person_id,
    platform) WHERE kind = 'welcome' AND status <> 'cancelled'`; `COMMIT`.
    A refusal rolls the whole file back with nothing applied, and because
    `scripts/apply-deploy-migrations.sh` runs psql with `ON_ERROR_STOP=1`
    under `set -euo pipefail`, the deploy stops there; the fix is to cancel
    the extra welcome and apply again. Old code that predates the matching
    `except (psycopg.errors.UniqueViolation,
    psycopg.errors.SerializationFailure)` handler, which answers 409, in
    `post_growth_spotlight_welcome` (`service/api/admin/spotlight_routes.py`,
    added in `3fb97e3`, so absent from the `5500740` rollback target) does
    **not** catch the resulting conflict: a race
    that used to silently create duplicate welcome rows now raises an
    unhandled integrity error on the second, concurrent insert instead of
    the newer code's clean `409`. This is the one migration on this list
    where "runs cleanly" needs a caveat: the schema change is compatible,
    but a code rollback to before the corresponding application fix can
    surface a 500 on a race that previously succeeded quietly. It does not
    corrupt data or block deploys either way.
  - `0051_spotlight_render_backoff.sql` -- one `ALTER TABLE
    publishing_queue` adding three columns, each `ADD COLUMN IF NOT EXISTS`:
    `render_attempts int NOT NULL DEFAULT 0`, `render_next_attempt_at
    timestamptz` and `render_error text` (both nullable, no default). Purely
    additive: nothing is dropped, renamed or constrained. Old code runs on
    it: the only `INSERT INTO publishing_queue` at `5500740`
    (`service/spotlight/queue.py`, `create_candidate`) names its columns, so
    the new ones take their defaults, and nothing in that code reads them
    (the claim function returns whole rows, so the old claim response
    carries them along unused). What the old code loses is the behaviour,
    not correctness: its render listing ignores the backoff and serves the
    newest 200 first again.

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

2. **Before restarting `cron`, check what mail is actually queued and hold
   anything that must not go out.** This is the last moment it can still be
   stopped: the moment the container comes back, `email_outbox_forever`
   drains every due row on its next poll. Read-only, grouped by campaign and
   state (`service/campaigns/outbox.py`, `STATES`):

   ```sql
   SELECT campaign, state, count(*) AS n
     FROM email_outbox
    GROUP BY campaign, state
    ORDER BY campaign, state;
   ```

   To see the actual queued rows for one campaign (for example a bad E4
   batch), read-only:

   ```sql
   SELECT id, campaign_id, email, next_attempt_at
     FROM email_outbox
    WHERE campaign = '<campaign>' AND state = 'queued'
    ORDER BY next_attempt_at;
   ```

   If any of those rows must not send, hold them with `state = 'skipped'`,
   not `'failed'` or `'acceptance_unknown'`: `reserve()`'s claim predicate
   only ever picks up `state = 'queued'` rows
   (`service/campaigns/outbox.py`, `_Q_RESERVE`), so `'skipped'`, `'failed'`
   and `'acceptance_unknown'` are all terminal as far as the drain is
   concerned, but `'skipped'` is the state the codebase itself already uses
   for "nothing went wrong, we simply must not send it" (`mark_skipped`'s
   own docstring; it is the same state and reason string `withdraw_member`
   writes directly onto a queued Spotlight invite when a member withdraws).
   `email_outbox` has no separate "updated at" column to set alongside it;
   `last_error` is the one column `_Q_SKIP` sets with the state, and it
   doubles as the reason field. Wrapped in a transaction so the count can be
   reviewed before it commits:

   ```sql
   BEGIN;
   UPDATE email_outbox
      SET state = 'skipped', last_error = 'operator hold: <why>'
    WHERE campaign = '<campaign>' AND state = 'queued'
   -- add "AND campaign_id = '<campaign_id>'" to hold one run only
   RETURNING id, campaign_id, email;
   -- if the returned rows are exactly the ones that must not send, COMMIT;
   -- otherwise ROLLBACK and narrow the WHERE.
   ```

   Run this only for mail a human has already decided must not send; it is
   irreversible in the sense that a skipped row is never picked up again by
   this drain.

3. **Restart the droplet's `cron` container** (section 2), as soon as step 1
   is confirmed and any mail that had to be held is held: it also runs NSFW
   photo scoring and removal, automated child-safety reporting, push
   notifications, identity verification, the FireHOL blocklist writer and
   the GDPR pending-deletion hard-delete, none of which should stay paused
   any longer than the incident actually required. `docker compose -f
   docker-compose.yml -f docker-compose.production.yml --env-file
   .env.production up -d cron`. Confirm: `docker compose ... ps cron` shows
   it running with no recent restarts, `docker logs --tail 20 <cron
   container>` shows no traceback, and if mail was queued during the
   outage, the `sent_since_stop` query from section 2 starts returning a
   growing count again for whatever was not held back.

4. **Re-enable the Vercel crons** (section 3), whichever option was used:
   put `CRON_SECRET` back in Vercel and redeploy, or restore the three
   entries in `ahavah-admin/vercel.json` and push. Only do this once all of
   these hold, per the Wave 3d deploy conditions
   (`docs/superpowers/plans/2026-09-16-spotlight-wave-3d.md`):
   - the admin app is on a Wave 3d head, not `30daef1` (section 4: the old
     tick uploads PNG, treats `image_race` as a failure and posts with
     `message`). While the admin is on `30daef1`, `CRON_SECRET` and
     `AHAVAH_GROWTH_CRON_SECRET` stay unset;
   - `AHAVAH_META_APP_ID` and `AHAVAH_META_APP_SECRET` are already set in
     the admin project, before `CRON_SECRET`. Without them the daily
     token-health cron cannot check the page token and answers `valid: null`
     without reporting anything to the API (`reportTokenHealth` in
     `ahavah-admin/src/lib/publishing.ts`);
   - `AHAVAH_GROWTH_CRON_SECRET` holds the same value in the admin project
     and in the API's `.env.production` (`.env.production.template`); with it
     unset or different, every cron call the admin app makes to the API is
     refused.

   Confirm: Vercel's Cron Jobs page shows the three schedules active again,
   and `GET /api/growth/publish-due?dry=1` with `Authorization: Bearer
   <CRON_SECRET>` answers 200 with `publish.dry` true. Use the dry run for
   this check, not a plain call: a plain call is a real run.

5. **Turn `publication_enabled` and `external_access_enabled` back on**
   (Growth tab -> Controls, or the same `POST /admin/growth/settings` calls
   as section 1 with `"value": "true"`) only after steps 1 to 4 are
   confirmed, and only if section 4's coupling rules are met (every repo on
   the same side of Wave 3d). Confirm, read-only: `GET
   /admin/growth/settings` shows `"publication_enabled": "true"` and
   `"external_access_enabled": "true"`, and `GET
   /api/growth/publish-due?dry=1` answers `publish.paused` false,
   `publish.halted` false and `removals.halted` false. The next scheduled
   `publish-due` run then returns `"dry": false` with `publish.paused` and
   `publish.halted` false; `publish.claimed` is a count and is legitimately
   0 when nothing is due. Do not use `POST /admin/growth/queue/claim` to
   check (section 1).

6. **Turn `invites_enabled` back on last.** Before flipping it, check the
   Growth tab's queue for anything that piled up while invites were paused
   (an ageing `invites_pending_oldest_days` on `GET /admin/growth/candidates`
   is the signal), and check the removal list is not carrying an unexpected
   backlog from the incident. Confirm: the switch reads on, and the next
   tick resumes creating welcome and roundup candidates
   (`welcomes_created` and `roundup_created` in the tick's own result move
   again instead of staying at zero).
