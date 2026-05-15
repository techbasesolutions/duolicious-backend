# Ahavah Postgres backups

Automated daily `pg_dump` of the `duo_api` database to DigitalOcean
Spaces. Wired via root cron on the production droplet (`167.71.93.27`)
at 03:00 UTC daily, with 7-day retention.

## What's where

- **Script:** `/opt/ahavah-api/backups/run.sh` (this directory, mirrored
  to droplet via `git pull`).
- **Cron entry:** `0 3 * * * /opt/ahavah-api/backups/run.sh >> /var/log/ahavah-backup.log 2>&1`
- **Storage:** `s3://ahavah-photos-prod/backups/ahavah-pg-<TS>.sql.gz`
- **Retention:** 7 days (the script sweeps older objects after each run).
- **Spaces credentials:** `/root/.aws/credentials` profile `ahavah-spaces`.
  Generated 2026-05-15 via the DO Spaces Keys API, scoped `readwrite`
  to bucket `ahavah-photos-prod`.

## Restore

To restore the database from a backup, on the droplet:

```bash
# 1. Pick the timestamp you want (most recent shown first)
aws --profile ahavah-spaces \
    --endpoint-url=https://nyc3.digitaloceanspaces.com \
    s3 ls s3://ahavah-photos-prod/backups/

# 2. Download the chosen dump
aws --profile ahavah-spaces \
    --endpoint-url=https://nyc3.digitaloceanspaces.com \
    s3 cp s3://ahavah-photos-prod/backups/ahavah-pg-<TS>.sql.gz /tmp/

# 3. Decompress + pipe into the postgres container.
#    NOTE: this REPLACES the current database. If you want to side-load
#    into a fresh DB instead, create a new database first and target it.
gunzip /tmp/ahavah-pg-<TS>.sql.gz
docker exec -i ahavah-api-postgres-1 \
  psql -U postgres -d duo_api < /tmp/ahavah-pg-<TS>.sql
```

To restore into a **new** database (safer — preserves the broken state
for forensics):

```bash
docker exec ahavah-api-postgres-1 \
  psql -U postgres -c "CREATE DATABASE duo_api_restore"
docker exec -i ahavah-api-postgres-1 \
  psql -U postgres -d duo_api_restore < /tmp/ahavah-pg-<TS>.sql
# then update DUO_DB_NAME in .env.production + restart api
```

## Verification

Confirm the cron is firing and uploads land in Spaces:

```bash
# Check recent runs
tail -50 /var/log/ahavah-backup.log

# List backups in Spaces
aws --profile ahavah-spaces \
    --endpoint-url=https://nyc3.digitaloceanspaces.com \
    s3 ls s3://ahavah-photos-prod/backups/
```

## Disaster recovery RTO/RPO (current)

- **RPO** (data loss tolerance): up to 24 hours (last nightly backup).
- **RTO** (time to restore): ~5 minutes for a database of the current
  size (<100MB compressed). Will scale linearly until DB > 10GB, at
  which point we should switch to point-in-time recovery via
  DigitalOcean Managed Database (separate runbook).

## Future improvements

- Switch to DO Managed Database for sub-minute PITR (requires
  postgres-on-droplet → managed migration).
- Add a Sentry/Healthchecks.io ping at the end of `run.sh` so silent
  cron failures get surfaced.
- Encrypted backups (GPG) before upload — currently relies on Spaces
  bucket access control, which is sufficient but defense-in-depth
  matters for PII.
