-- NexusDesk PR44 WebChat AI Turn Runtime candidate migration.
-- PostgreSQL-oriented SQL. Review before production use. Do NOT run blindly against production.

BEGIN;

CREATE TABLE IF NOT EXISTS webchat_ai_turns (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES webchat_conversations(id),
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    trigger_message_id INTEGER NOT NULL REFERENCES webchat_messages(id),
    latest_visitor_message_id INTEGER NULL REFERENCES webchat_messages(id),
    context_cutoff_message_id INTEGER NULL REFERENCES webchat_messages(id),
    job_id INTEGER NULL REFERENCES background_jobs(id),
    status VARCHAR(40) NOT NULL DEFAULT 'queued',
    status_reason TEXT NULL,
    reply_message_id INTEGER NULL REFERENCES webchat_messages(id),
    reply_source VARCHAR(80) NULL,
    fallback_reason TEXT NULL,
    fact_gate_reason TEXT NULL,
    bridge_elapsed_ms INTEGER NULL,
    bridge_timeout_ms INTEGER NULL,
    superseded_by_turn_id INTEGER NULL REFERENCES webchat_ai_turns(id),
    is_public_reply_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_webchat_ai_turn_trigger_message ON webchat_ai_turns(trigger_message_id);
CREATE INDEX IF NOT EXISTS ix_webchat_ai_turns_conversation_id ON webchat_ai_turns(conversation_id);
CREATE INDEX IF NOT EXISTS ix_webchat_ai_turns_ticket_id ON webchat_ai_turns(ticket_id);
CREATE INDEX IF NOT EXISTS ix_webchat_ai_turns_status ON webchat_ai_turns(status);
CREATE INDEX IF NOT EXISTS ix_webchat_ai_turns_updated_at ON webchat_ai_turns(updated_at);

CREATE TABLE IF NOT EXISTS webchat_events (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES webchat_conversations(id),
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    event_type VARCHAR(80) NOT NULL,
    payload_json TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_webchat_events_conversation_id ON webchat_events(conversation_id);
CREATE INDEX IF NOT EXISTS ix_webchat_events_ticket_id ON webchat_events(ticket_id);
CREATE INDEX IF NOT EXISTS ix_webchat_events_event_type ON webchat_events(event_type);
CREATE INDEX IF NOT EXISTS ix_webchat_events_created_at ON webchat_events(created_at);

ALTER TABLE webchat_conversations
ADD COLUMN IF NOT EXISTS active_ai_turn_id INTEGER NULL,
ADD COLUMN IF NOT EXISTS active_ai_status VARCHAR(40) NULL,
ADD COLUMN IF NOT EXISTS active_ai_for_message_id INTEGER NULL,
ADD COLUMN IF NOT EXISTS active_ai_context_cutoff_message_id INTEGER NULL,
ADD COLUMN IF NOT EXISTS next_ai_turn_id INTEGER NULL,
ADD COLUMN IF NOT EXISTS active_ai_started_at TIMESTAMPTZ NULL,
ADD COLUMN IF NOT EXISTS active_ai_updated_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_webchat_conversations_active_ai_turn_id ON webchat_conversations(active_ai_turn_id);
CREATE INDEX IF NOT EXISTS ix_webchat_conversations_active_ai_status ON webchat_conversations(active_ai_status);
CREATE INDEX IF NOT EXISTS ix_webchat_conversations_active_ai_for_message_id ON webchat_conversations(active_ai_for_message_id);
CREATE INDEX IF NOT EXISTS ix_webchat_conversations_active_ai_context_cutoff_message_id ON webchat_conversations(active_ai_context_cutoff_message_id);
CREATE INDEX IF NOT EXISTS ix_webchat_conversations_next_ai_turn_id ON webchat_conversations(next_ai_turn_id);
CREATE INDEX IF NOT EXISTS ix_webchat_conversations_active_ai_updated_at ON webchat_conversations(active_ai_updated_at);

COMMIT;
