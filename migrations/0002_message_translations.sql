-- Phase 2 Task 2.2 — message-table additions for lazy translate-on-read.
--
-- mam_message is duolicious's XMPP-archive table. `search_body` is the plain
-- text we translate; the binary `message` field carries the XMPP envelope
-- and stays untouched. Translations land in JSONB so multiple target-lang
-- variants can coexist on a single row (recipient A wants EN-US, recipient B
-- wants JA — both stored).

BEGIN;

ALTER TABLE mam_message
  ADD COLUMN IF NOT EXISTS detected_source_lang CHAR(2),                                -- e.g. 'EN', 'JA'
  ADD COLUMN IF NOT EXISTS translations         JSONB NOT NULL DEFAULT '{}'::jsonb;     -- {"EN-US": "Hello, …", "JA": "こんにちは…"}

CREATE INDEX IF NOT EXISTS idx__mam_message__translations
  ON mam_message USING GIN (translations);

-- Profile-side fields for translation behavior were added in migration 0001:
--   person.primary_language        — the user's translate-INTO target
--   person.auto_translate_enabled  — opt-out flag, default TRUE

COMMIT;
