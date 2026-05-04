-- NexusDesk PR44 WebChat AI Turn Runtime rollback migration.
-- PostgreSQL-oriented rollback SQL.
-- Use only if PR44 code is rolled back and the new AI turn schema must be removed.
-- Safer default in production is usually to keep nullable additive columns/tables.
-- Run manually only after verified DB backup.

BEGIN;

DROP INDEX IF EXISTS ix_webchat_conversations_active_ai_updated_at;
DROP INDEX IF EXISTS ix_webchat_conversations_next_ai_turn_id;
DROP INDEX IF EXISTS ix_webchat_conversations_active_ai_context_cutoff_message_id;
DROP INDEX IF EXISTS ix_webchat_conversations_active_ai_for_message_id;
DROP INDEX IF EXISTS ix_webchat_conversations_active_ai_status;
DROP INDEX IF EXISTS ix_webchat_conversations_active_ai_turn_id;

ALTER TABLE webchat_conversations
  DROP COLUMN IF EXISTS active_ai_updated_at,
  DROP COLUMN IF EXISTS active_ai_started_at,
  DROP COLUMN IF EXISTS next_ai_turn_id,
  DROP COLUMN IF EXISTS active_ai_context_cutoff_message_id,
  DROP COLUMN IF EXISTS active_ai_for_message_id,
  DROP COLUMN IF EXISTS active_ai_status,
  DROP COLUMN IF EXISTS active_ai_turn_id;

DROP INDEX IF EXISTS ix_webchat_events_created_at;
DROP INDEX IF EXISTS ix_webchat_events_event_type;
DROP INDEX IF EXISTS ix_webchat_events_ticket_id;
DROP INDEX IF EXISTS ix_webchat_events_conversation_id;
DROP TABLE IF EXISTS webchat_events;

DROP INDEX IF EXISTS ix_webchat_ai_turns_updated_at;
DROP INDEX IF EXISTS ix_webchat_ai_turns_status;
DROP INDEX IF EXISTS ix_webchat_ai_turns_ticket_id;
DROP INDEX IF EXISTS ix_webchat_ai_turns_conversation_id;
DROP INDEX IF EXISTS uq_webchat_ai_turn_trigger_message;
DROP TABLE IF EXISTS webchat_ai_turns;

COMMIT;
