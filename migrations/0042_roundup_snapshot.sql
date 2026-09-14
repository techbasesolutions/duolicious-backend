-- migrations/0042_roundup_snapshot.sql
ALTER TABLE publishing_queue ADD COLUMN IF NOT EXISTS payload jsonb;
