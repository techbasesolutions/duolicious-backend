-- migrations/0045_spotlight_controls.sql
-- Task 9 (F12): three named controls replace the one `scheduler_enabled`
-- switch that used to gate publishing and removals together while the tick
-- ran regardless. `auto_welcome`/`auto_roundup` were stored and never read.
-- `invites_enabled`, `publication_enabled` and `external_access_enabled`
-- (seeded false/false/true by migration 0044) are the surviving controls.
-- Idempotent.
DELETE FROM spotlight_setting WHERE key IN ('scheduler_enabled','auto_welcome','auto_roundup');
