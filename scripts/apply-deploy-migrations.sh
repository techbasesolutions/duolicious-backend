#!/usr/bin/env bash
# Checkpoint successful migrations, reject drift, and stop at the failing file.
set -euo pipefail
container="${AHAVAH_POSTGRES_CONTAINER:-ahavah-api-postgres-1}"
database="${AHAVAH_MIGRATION_DATABASE:-duo_api}"
script=$(mktemp)
trap 'rm -f "$script"' EXIT
docker exec "$container" mkdir -p /tmp/ahavah-migrations
docker cp migrations/. "$container":/tmp/ahavah-migrations/
cat > "$script" <<'SQL'
SELECT pg_advisory_lock(782344102);
CREATE TABLE IF NOT EXISTS ahavah_schema_migration (
  filename text PRIMARY KEY,
  checksum text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
SQL
for file in migrations/*.sql; do
  name=$(basename "$file")
  [[ "$name" =~ ^[a-zA-Z0-9_-]+\.sql$ ]] || { echo "Invalid migration filename" >&2; exit 1; }
  checksum=$(sha256sum "$file" | cut -d ' ' -f 1)
  cat >> "$script" <<SQL
\echo Checking $name
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM ahavah_schema_migration WHERE filename = '$name' AND checksum <> '$checksum') THEN
    RAISE EXCEPTION 'Migration checksum changed: $name';
  END IF;
END \$\$;
SELECT EXISTS (SELECT 1 FROM ahavah_schema_migration WHERE filename = '$name') AS applied \gset
\if :applied
\echo Already applied $name
\else
\echo Applying $name
\i /tmp/ahavah-migrations/$name
INSERT INTO ahavah_schema_migration (filename, checksum) VALUES ('$name', '$checksum');
\endif
SQL
done
# Session-level advisory lock survives transaction boundaries inside old files.
# On the first tracked deployment, legacy files are actually executed using the
# existing idempotent upgrade path; they are not blindly marked as applied.
docker exec -i "$container" psql -X -v ON_ERROR_STOP=1 -U postgres -d "$database" < "$script"
