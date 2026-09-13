# Community Spotlight, Phase A: pre-flight record (2026-09-13)

Spec: `docs/superpowers/specs/2026-09-13-community-spotlight-design.md`, section 8.
Plan: `docs/superpowers/plans/2026-09-13-community-spotlight-phase-a.md`, Task 1.

All checks below are read only. No role was granted, no job was deleted, and no
production data was changed while gathering this record. The four owner
decisions this record surfaces are listed first.

## Owner decisions still open

1. Meta app and permissions (spec 8.1): confirm whether the app behind the
   Chapman token can be granted the Ahavah Page and Instagram account with
   `pages_manage_posts` and `instagram_content_publish`, or register and
   review a new app. Blocking for the posting side of Phase B; not checked
   here because it requires the Meta developer console, not a read-only
   droplet or database query.
2. Vercel plan on the ahavah-admin project (spec 8.2): the CLI is not
   authenticated in this environment, so plan support for a per-minute cron
   could not be confirmed automatically. See finding 3 below.
3. Grant the admin role to the owner's own account (spec 8.3): not done here.
   Finding 2 below confirms the account does not currently hold it, so this
   remains an explicit owner action.
4. Reconcile any scheduled email waves on the droplet (spec 8.4): finding 1
   below lists what is actually scheduled; no digest/weekly-email collision
   risk was found, but the owner should confirm no wave is pending outside
   `atq`/`crontab`/`cron.d` (for example, a queued job inside the API
   process itself).

## Finding 1: pending scheduled email waves (spec 8.4)

Command:

```bash
ssh -i ~/.ssh/id_ed25519_ahavah -6 root@2604:a880:800:14:0:2:f28f:5000 'atq; crontab -l 2>/dev/null | grep -v "^#"; ls /etc/cron.d'
```

Output:

```
bash: line 1: atq: command not found
0 3 * * * /opt/ahavah-api/backups/run.sh >> /var/log/ahavah-backup.log 2>&1
certbot
e2scrub_all
```

Reading: `atq` (the `at` command queue) is not installed on the droplet, so
there is no `at`-scheduled job to report. The root crontab has exactly one
entry, a nightly backup at 03:00, unrelated to member email. `/etc/cron.d`
holds only the system `certbot` and `e2scrub_all` entries. No email wave
(digest or weekly community email) is currently scheduled anywhere the shell
can see. This clears the collision risk spec 8.4 is guarding against, but see
owner decision 4 above.

## Finding 2: owner account admin role (spec 8.3)

The brief's query assumed `person_role` and `role` tables. Neither exists.
`\dt` against `duo_api` lists 96 tables; the closest match is a `roles`
column (type `ARRAY`) directly on `person`. The actual role model is a text
array on `person.roles`, not a join table.

Discovery command:

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='person' AND (column_name ILIKE '%admin%' OR column_name ILIKE '%role%');
```

Output: `roles | ARRAY` (one row).

Adapted query and result, run against the real column:

```sql
SELECT email, roles FROM person WHERE 'admin' = ANY(roles);
```

```
      email       |  roles
------------------+---------
 admin@ahavah.app | {admin}
```

Targeted check for the owner's own account:

```sql
SELECT email, roles FROM person WHERE email='harrigan.tennyson@gmail.com' AND 'admin' = ANY(roles);
```

```
 email | roles
-------+-------
(0 rows)
```

Reading: 0 rows for `harrigan.tennyson@gmail.com` today, as expected. Only
`admin@ahavah.app` currently carries the `admin` role. Granting the role to
the owner's own account remains owner decision 3 above; when it happens, use
`UPDATE person SET roles = array_append(roles, 'admin') WHERE email = '...'`
against the real `person.roles` array, not a `person_role`/`role` join
(those tables do not exist in this schema).

## Finding 3: Vercel plan for the admin project (spec 8.2)

Commands:

```bash
cd /d/Antigravity/ahavah-admin && npx vercel project ls 2>/dev/null | head -5
npx vercel teams ls 2>/dev/null | head -5
```

Output (both commands):

```
Vercel CLI 50.22.1
Error: No existing credentials found. Please run `vercel login` or pass "--token"
```

Reading: vercel CLI not authenticated; owner to confirm plan. Per-minute cron
support on the ahavah-admin project's Vercel plan could not be checked from
this environment.

## Finding 4: Meta app and permissions (spec 8.1)

Owner action, not a read-only check available from this environment. See
owner decision 1 above.

## Preference tables (Task 6)

Command:

```bash
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml exec -T postgres \
  psql -U postgres -d duo_api -c "SELECT table_name FROM information_schema.tables WHERE table_name IN ('search_preference_age','search_preference_gender');"
```

Output: both tables present (`search_preference_age`, `search_preference_gender`).

Column check (`\d search_preference_age`, `\d search_preference_gender`):

```
search_preference_age:    person_id (integer, PK, FK -> person.id), min_age (smallint), max_age (smallint)
search_preference_gender: person_id (integer, part of PK), gender_id (smallint, part of PK, FK -> gender.id)
```

Reading: the brief's assumed column names (`person_id`, `gender_id`, `min_age`,
`max_age`) match the real schema exactly. No adaptation was needed in
`_Q_NEWCOMERS`; the queries in the brief were implemented verbatim. The
activity tables the brief names (`liked(liker_id, liked_id, created_at)`,
`skipped(subject_person_id, object_person_id, created_at)`,
`messaged(subject_person_id, object_person_id, created_at)`,
`ahavah_match(match_id, user_a_id, user_b_id, created_at)`,
`photo(person_id, ...)`) and the relevant `person` columns (`activated`,
`country`, `date_of_birth`, `deletion_requested_at`, `spotlight_opt_in`,
`reinvite_sent_at`, `subscription_expires_at`, `gender_id`, `sign_up_time`,
`email`, `name`) were also checked and match the brief exactly.
