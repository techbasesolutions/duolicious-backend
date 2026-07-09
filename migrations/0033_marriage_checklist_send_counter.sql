-- Anonymous completions counter for the marriage checklist. One row per
-- successful send, timestamp only. Deliberately NO email, NO answers, NO
-- person reference: the activity promises "we never store your answers",
-- and this table stores nothing about who completed it or what they said.
CREATE TABLE IF NOT EXISTS marriage_checklist_send (
    id BIGSERIAL PRIMARY KEY,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
