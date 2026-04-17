from enum import Enum


class UserRole(str, Enum):
    admin = "admin"
    manager = "manager"
    lead = "lead"
    agent = "agent"
    auditor = "auditor"


class TicketSource(str, Enum):
    manual = "manual"
    user_message = "user_message"
    ai_intake = "ai_intake"
    api = "api"


class SourceChannel(str, Enum):
    whatsapp = "whatsapp"
    email = "email"
    web_chat = "web_chat"
    internal = "internal"


class TicketPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TicketStatus(str, Enum):
    new = "new"
    pending_assignment = "pending_assignment"
    in_progress = "in_progress"
    waiting_customer = "waiting_customer"
    waiting_internal = "waiting_internal"
    escalated = "escalated"
    resolved = "resolved"
    closed = "closed"
    canceled = "canceled"


class ResolutionCategory(str, Enum):
    none = "none"
    solved = "solved"
    duplicate = "duplicate"
    no_response = "no_response"
    invalid_request = "invalid_request"
    canceled_by_request = "canceled_by_request"


class ConversationState(str, Enum):
    ai_active = "ai_active"
    human_review_required = "human_review_required"
    human_owned = "human_owned"
    ready_to_reply = "ready_to_reply"
    replied_to_customer = "replied_to_customer"
    waiting_customer = "waiting_customer"
    reopened_by_customer = "reopened_by_customer"


class EventType(str, Enum):
    ticket_created = "ticket_created"
    status_changed = "status_changed"
    assigned = "assigned"
    escalated = "escalated"
    reopened = "reopened"
    comment_added = "comment_added"
    internal_note_added = "internal_note_added"
    attachment_added = "attachment_added"
    outbound_draft_saved = "outbound_draft_saved"
    outbound_queued = "outbound_queued"
    outbound_sent = "outbound_sent"
    outbound_failed = "outbound_failed"
    outbound_retry_scheduled = "outbound_retry_scheduled"
    outbound_dead = "outbound_dead"
    ai_intake_added = "ai_intake_added"
    sla_breached = "sla_breached"
    field_updated = "field_updated"
    integration_request_received = "integration_request_received"
    openclaw_synced = "openclaw_synced"
    openclaw_reply_sent = "openclaw_reply_sent"
    openclaw_attachment_synced = "openclaw_attachment_synced"
    openclaw_attachment_persisted = "openclaw_attachment_persisted"
    conversation_state_changed = "conversation_state_changed"


class NoteVisibility(str, Enum):
    external = "external"
    internal = "internal"


class MessageStatus(str, Enum):
    draft = "draft"
    pending = "pending"
    processing = "processing"
    sent = "sent"
    failed = "failed"
    dead = "dead"


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
    dead = "dead"


class TimelineVisibility(str, Enum):
    public = "public"
    internal = "internal"
