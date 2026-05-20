-- 0016_message_reactions.sql
-- Per-message chat reactions (v1: heart only). The deploy re-runs every
-- migration every time and swallows errors, so every statement here is
-- guarded (IF NOT EXISTS) and safe to run repeatedly.
--
-- message_stanza_id is the client-generated UUID stamped on the message
-- stanza (also the MAM row id on history fetch); both participants
-- reference the same logical message by it. No FK to MAM (MAM exposes no
-- single-row PK). PK (message_stanza_id, reactor_id) = one reaction per
-- person per message (toggle model). peer_id makes the per-conversation
-- hydrate query cheap. kind column present from day one so multi-emoji is
-- a non-breaking add later.

BEGIN;

CREATE TABLE IF NOT EXISTS message_reactions (
  message_stanza_id  TEXT         NOT NULL,
  reactor_id         INTEGER      NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  peer_id            INTEGER      NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  kind               TEXT         NOT NULL DEFAULT 'heart',
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (message_stanza_id, reactor_id)
);

CREATE INDEX IF NOT EXISTS idx__message_reactions__reactor_id__peer_id
  ON message_reactions(reactor_id, peer_id);

COMMIT;
