from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import NoteVisibility, TicketStatus, UserRole
from ..models import UserCapabilityOverride

CAP_TICKET_READ = "ticket.read"
CAP_TICKET_ASSIGN = "ticket.assign"
CAP_TICKET_ESCALATE = "ticket.escalate"
CAP_TICKET_UPDATE_CORE = "ticket.update_core"
CAP_TICKET_STATUS_CHANGE = "ticket.status.change"
CAP_TICKET_CLOSE = "ticket.close"
CAP_ATTACHMENT_READ_EXTERNAL = "attachment.read.external"
CAP_ATTACHMENT_READ_INTERNAL = "attachment.read.internal"
CAP_ATTACHMENT_UPLOAD = "attachment.upload"
CAP_CUSTOMER_PROFILE_READ = "customer_profile.read"
CAP_OUTBOUND_DRAFT_SAVE = "outbound.draft.save"
CAP_OUTBOUND_SEND = "outbound.send"
CAP_AI_INTAKE_WRITE = "ai_intake.write"
CAP_NOTE_WRITE_INTERNAL = "note.write.internal"
CAP_NOTE_WRITE_EXTERNAL = "note.write.external"
CAP_USER_MANAGE = "user.manage"
CAP_CHANNEL_ACCOUNT_MANAGE = "channel_account.manage"
CAP_BULLETIN_MANAGE = "bulletin.manage"
CAP_AI_CONFIG_MANAGE = "ai_config.manage"
CAP_RUNTIME_MANAGE = "runtime.manage"
CAP_MARKET_MANAGE = "market.manage"

ALL_CAPABILITIES = [
    CAP_TICKET_READ,
    CAP_TICKET_ASSIGN,
    CAP_TICKET_ESCALATE,
    CAP_TICKET_UPDATE_CORE,
    CAP_TICKET_STATUS_CHANGE,
    CAP_TICKET_CLOSE,
    CAP_ATTACHMENT_READ_EXTERNAL,
    CAP_ATTACHMENT_READ_INTERNAL,
    CAP_ATTACHMENT_UPLOAD,
    CAP_CUSTOMER_PROFILE_READ,
    CAP_OUTBOUND_DRAFT_SAVE,
    CAP_OUTBOUND_SEND,
    CAP_AI_INTAKE_WRITE,
    CAP_NOTE_WRITE_INTERNAL,
    CAP_NOTE_WRITE_EXTERNAL,
    CAP_USER_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_BULLETIN_MANAGE,
    CAP_AI_CONFIG_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_MARKET_MANAGE,
]

ROLE_CAPABILITIES: dict[UserRole, set[str]] = {
    UserRole.admin: set(ALL_CAPABILITIES),
    UserRole.manager: {
        CAP_TICKET_READ, CAP_TICKET_ASSIGN, CAP_TICKET_ESCALATE, CAP_TICKET_UPDATE_CORE,
        CAP_TICKET_STATUS_CHANGE, CAP_TICKET_CLOSE, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_ATTACHMENT_UPLOAD, CAP_CUSTOMER_PROFILE_READ,
        CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE,
        CAP_NOTE_WRITE_INTERNAL, CAP_NOTE_WRITE_EXTERNAL,
        CAP_USER_MANAGE, CAP_CHANNEL_ACCOUNT_MANAGE, CAP_BULLETIN_MANAGE,
        CAP_AI_CONFIG_MANAGE, CAP_RUNTIME_MANAGE, CAP_MARKET_MANAGE,
    },
    UserRole.lead: {
        CAP_TICKET_READ, CAP_TICKET_ASSIGN, CAP_TICKET_ESCALATE, CAP_TICKET_UPDATE_CORE,
        CAP_TICKET_STATUS_CHANGE, CAP_TICKET_CLOSE, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_ATTACHMENT_UPLOAD, CAP_CUSTOMER_PROFILE_READ,
        CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE,
        CAP_NOTE_WRITE_INTERNAL, CAP_NOTE_WRITE_EXTERNAL,
    },
    UserRole.agent: {
        CAP_TICKET_READ, CAP_ATTACHMENT_READ_EXTERNAL, CAP_ATTACHMENT_READ_INTERNAL,
        CAP_ATTACHMENT_UPLOAD, CAP_TICKET_STATUS_CHANGE, CAP_OUTBOUND_DRAFT_SAVE,
        CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE, CAP_NOTE_WRITE_INTERNAL,
        CAP_NOTE_WRITE_EXTERNAL, CAP_CUSTOMER_PROFILE_READ,
    },
    UserRole.auditor: {
        CAP_TICKET_READ, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_CUSTOMER_PROFILE_READ,
    },
}


def _base_capabilities(role: UserRole) -> set[str]:
    return set(ROLE_CAPABILITIES.get(role, set()))


def resolve_capabilities(user, db: Session | None = None) -> set[str]:
    capabilities = _base_capabilities(user.role)
    if db is None:
        return capabilities
    overrides = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user.id).all()
    for override in overrides:
        if override.allowed:
            capabilities.add(override.capability)
        else:
            capabilities.discard(override.capability)
    return capabilities


def ensure_capability(user, capability: str, db: Session | None = None, *, message: str = "Permission denied") -> None:
    if capability not in resolve_capabilities(user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def ensure_ticket_visible(user, ticket, db: Session | None = None):
    ensure_capability(user, CAP_TICKET_READ, db, message="Ticket not visible for current user")
    if user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return
    if ticket.assignee_id == user.id:
        return
    if user.team_id and ticket.team_id == user.team_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ticket not visible for current user")


def ensure_attachment_accessible(user, attachment, db: Session | None = None):
    ticket = getattr(attachment, "ticket", None)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Attachment ticket relationship is not loaded")
    ensure_ticket_visible(user, ticket, db)
    if attachment.visibility == NoteVisibility.external:
        ensure_capability(user, CAP_ATTACHMENT_READ_EXTERNAL, db, message="Attachment is not accessible")
        return
    ensure_capability(user, CAP_ATTACHMENT_READ_INTERNAL, db, message="Attachment is internal-only")


def ensure_can_assign(user, db: Session | None = None):
    ensure_capability(user, CAP_TICKET_ASSIGN, db, message="Only lead or above can assign")


def ensure_can_escalate(user, db: Session | None = None):
    ensure_capability(user, CAP_TICKET_ESCALATE, db, message="Only lead or above can escalate")


def ensure_can_update_core_fields(user, db: Session | None = None):
    ensure_capability(user, CAP_TICKET_UPDATE_CORE, db, message="Only lead or above can update core fields")


def ensure_can_change_status(user, ticket, new_status, db: Session | None = None):
    capabilities = resolve_capabilities(user, db)
    if CAP_TICKET_STATUS_CHANGE not in capabilities:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    privileged_close = CAP_TICKET_CLOSE in capabilities
    if new_status in {TicketStatus.closed, TicketStatus.canceled, TicketStatus.escalated} and not privileged_close:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if user.role == UserRole.agent:
        if ticket.assignee_id != user.id and ticket.team_id != user.team_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent cannot operate this ticket")
        if new_status in {TicketStatus.closed, TicketStatus.canceled, TicketStatus.escalated} and not privileged_close:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent cannot perform this status change")


def ensure_can_upload_attachment(user, db: Session | None = None):
    ensure_capability(user, CAP_ATTACHMENT_UPLOAD, db, message="Permission denied")


def ensure_can_read_customer_profile(user, db: Session | None = None):
    ensure_capability(user, CAP_CUSTOMER_PROFILE_READ, db, message="Permission denied")


def ensure_can_save_outbound_draft(user, db: Session | None = None):
    ensure_capability(user, CAP_OUTBOUND_DRAFT_SAVE, db, message="Permission denied")


def ensure_can_send_outbound(user, db: Session | None = None):
    ensure_capability(user, CAP_OUTBOUND_SEND, db, message="Permission denied")


def ensure_can_write_ai_intake(user, db: Session | None = None):
    ensure_capability(user, CAP_AI_INTAKE_WRITE, db, message="Permission denied")


def ensure_can_write_internal_note(user, db: Session | None = None):
    ensure_capability(user, CAP_NOTE_WRITE_INTERNAL, db, message="Permission denied")


def ensure_can_write_external_comment(user, db: Session | None = None):
    ensure_capability(user, CAP_NOTE_WRITE_EXTERNAL, db, message="Permission denied")


def ensure_can_write_comment(user, visibility: NoteVisibility, db: Session | None = None):
    if visibility == NoteVisibility.internal:
        ensure_can_write_internal_note(user, db)
        return
    ensure_can_write_external_comment(user, db)


def ensure_can_manage_users(user, db: Session | None = None):
    ensure_capability(user, CAP_USER_MANAGE, db, message="Not authorized to manage users")


def ensure_can_manage_channel_accounts(user, db: Session | None = None):
    ensure_capability(user, CAP_CHANNEL_ACCOUNT_MANAGE, db, message="Not authorized to manage channel accounts")


def ensure_can_manage_bulletins(user, db: Session | None = None):
    ensure_capability(user, CAP_BULLETIN_MANAGE, db, message="Not authorized to manage bulletins")


def ensure_can_manage_ai_configs(user, db: Session | None = None):
    ensure_capability(user, CAP_AI_CONFIG_MANAGE, db, message="Not authorized to manage AI config")


def ensure_can_manage_runtime(user, db: Session | None = None):
    ensure_capability(user, CAP_RUNTIME_MANAGE, db, message="Not authorized to manage runtime")


def ensure_can_manage_markets(user, db: Session | None = None):
    ensure_capability(user, CAP_MARKET_MANAGE, db, message="Not authorized to manage markets")
