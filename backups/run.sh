#!/bin/bash
# Ahavah Postgres backup — daily pg_dump + gzip + upload to DO Spaces.
#
# Wired to cron (root crontab on the production droplet) at 03:00 UTC daily:
#   0 3 * * * /opt/ahavah-api/backups/run.sh >> /var/log/ahavah-backup.log 2>&1
#
# Requires:
#   - aws-cli (apt install awscli) with profile `ahavah-spaces` in
#     /root/.aws/credentials, scoped to bucket `ahavah-photos-prod`
#     (readwrite). Generated via DO Spaces Keys API on 2026-05-15.
#   - The postgres docker container `ahavah-api-postgres-1` running.
#
# What it does:
#   1. pg_dump of the duo_api database from the running postgres container
#   2. gzip -9 the dump (typical 10-20× reduction)
#   3. Upload to s3://ahavah-photos-prod/backups/ahavah-pg-<TS>.sql.gz
#   4. Sweep local /tmp + remote backups older than 7 days
#
# Restore (manual, see README.md in this directory):
#   aws --profile ahavah-spaces --endpoint-url=https://nyc3.digitaloceanspaces.com \
#       s3 cp s3://ahavah-photos-prod/backups/<filename> ./
#   gunzip <filename>
#   docker exec -i ahavah-api-postgres-1 psql -U postgres -d duo_api < <filename%.gz>

set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-ahavah-api-postgres-1}"
BUCKET="${BUCKET:-ahavah-photos-prod}"
PREFIX="${PREFIX:-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
ENDPOINT="${ENDPOINT:-https://nyc3.digitaloceanspaces.com}"
AWS_PROFILE="${AWS_PROFILE:-ahavah-spaces}"

TS=$(date -u +%Y%m%dT%H%M%SZ)
FILE="ahavah-pg-${TS}.sql.gz"
LOCAL="/tmp/${FILE}"
REMOTE="s3://${BUCKET}/${PREFIX}/${FILE}"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "starting backup → ${REMOTE}"

# --- 1. dump + gzip ----------------------------------------------------------
docker exec "${PG_CONTAINER}" pg_dump -U postgres duo_api \
  | gzip -9 \
  > "${LOCAL}"

SIZE=$(stat -c %s "${LOCAL}")
log "dump complete: ${SIZE} bytes → ${LOCAL}"

if [ "${SIZE}" -lt 1024 ]; then
  log "ERROR: dump too small (<1KB), aborting upload"
  rm -f "${LOCAL}"
  exit 1
fi

# --- 2. upload ---------------------------------------------------------------
aws --profile "${AWS_PROFILE}" --endpoint-url="${ENDPOINT}" \
    s3 cp "${LOCAL}" "${REMOTE}"

log "upload OK"

# --- 3. clean local ----------------------------------------------------------
rm -f "${LOCAL}"

# --- 4. retention sweep ------------------------------------------------------
# DO Spaces doesn't support S3 lifecycle rules through the standard
# put-bucket-lifecycle-configuration API in all regions, so we sweep
# manually. Cheap — typical bucket has <10 backup objects.
CUTOFF_TS=$(date -u -d "-${RETENTION_DAYS} days" +%Y%m%dT%H%M%SZ)
log "sweeping backups older than ${CUTOFF_TS}"

aws --profile "${AWS_PROFILE}" --endpoint-url="${ENDPOINT}" \
    s3 ls "s3://${BUCKET}/${PREFIX}/" \
  | awk '{print $NF}' \
  | grep -E '^ahavah-pg-[0-9]{8}T[0-9]{6}Z\.sql\.gz$' \
  | while read -r OLD_FILE; do
      # Extract TS suffix from filename for comparison
      OLD_TS=$(printf '%s' "${OLD_FILE}" | sed -E 's/^ahavah-pg-([0-9TZ]+)\.sql\.gz$/\1/')
      if [ "${OLD_TS}" \< "${CUTOFF_TS}" ]; then
        log "  prune: ${OLD_FILE}"
        aws --profile "${AWS_PROFILE}" --endpoint-url="${ENDPOINT}" \
            s3 rm "s3://${BUCKET}/${PREFIX}/${OLD_FILE}"
      fi
    done

log "done"
