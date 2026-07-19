from __future__ import annotations

import hashlib

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import NoteVisibility, TicketStatus, UserRole
from ..models import UserCapabilityOverride
from .tenant_authority import ensure_ticket_tenant_authority

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
CAP_AI_CONFIG_READ = "ai_config.read"
CAP_AI_CONFIG_MANAGE = "ai_config.manage"
CAP_RUNTIME_MANAGE = "runtime.manage"
CAP_MARKET_MANAGE = "market.manage"
CAP_QA_MANAGE = "qa.manage"
CAP_SECURITY_READ = "security.read"
CAP_AUDIT_READ = "audit.read"
CAP_SPEEDAF_WORK_ORDER_WRITE = "tool:speedaf.work_order.create:write"
CAP_SPEEDAF_ADDRESS_UPDATE_WRITE = "tool:speedaf.order.update_address:write"
CAP_SPEEDAF_CANCEL_WRITE = "tool:speedaf.order.cancel:write"
CAP_SPEEDAF_VOICE_CALLBACK_WRITE = "tool:speedaf.voice.callback:write"
CAP_WEBCALL_VOICE_READ = "webcall.voice.read"
CAP_WEBCALL_VOICE_QUEUE_VIEW = "webcall.voice.queue.view"
CAP_WEBCALL_VOICE_ACCEPT = "webcall.voice.accept"
CAP_WEBCALL_VOICE_REJECT = "webcall.voice.reject"
CAP_WEBCALL_VOICE_END = "webcall.voice.end"
CAP_WEBCALL_VOICE_CONTROL = "webcall.voice.control"
CAP_WEBCHAT_HANDOFF_ACCEPT = "webchat.handoff.accept"
CAP_WEBCHAT_HANDOFF_DECLINE = "webchat.handoff.decline"
CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER = "webchat.handoff.force_takeover"
CAP_WEBCHAT_HANDOFF_RELEASE = "webchat.handoff.release"
CAP_WEBCHAT_HANDOFF_RESUME_AI = "webchat.handoff.resume_ai"
CAP_WEBCHAT_CONVERSATION_MONITOR_AI = "webchat.conversation.monitor_ai"
CAP_OPERATOR_QUEUE_READ = "operator_queue.read"

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
    CAP_AI_CONFIG_READ,
    CAP_AI_CONFIG_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_MARKET_MANAGE,
    CAP_QA_MANAGE,
    CAP_SECURITY_READ,
    CAP_AUDIT_READ,
    CAP_SPEEDAF_WORK_ORDER_WRITE,
    CAP_SPEEDAF_ADDRESS_UPDATE_WRITE,
    CAP_SPEEDAF_CANCEL_WRITE,
    CAP_SPEEDAF_VOICE_CALLBACK_WRITE,
    CAP_WEBCALL_VOICE_READ,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_REJECT,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_CONTROL,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
    CAP_OPERATOR_QUEUE_READ,
]

ROLE_CAPABILITIES: dict[UserRole, set[str]] = {
    UserRole.admin: set(ALL_CAPABILITIES),
    # Role defaults are centralized policy inputs only. Runtime authorization
    # consumes the resulting capability set and never branches on role names.
    UserRole.manager: {
        CAP_TICKET_READ, CAP_TICKET_ASSIGN, CAP_TICKET_ESCALATE, CAP_TICKET_UPDATE_CORE,
        CAP_TICKET_STATUS_CHANGE, CAP_TICKET_CLOSE, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_ATTACHMENT_UPLOAD, CAP_CUSTOMER_PROFILE_READ,
        CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE,
        CAP_NOTE_WRITE_INTERNAL, CAP_NOTE_WRITE_EXTERNAL, CAP_BULLETIN_MANAGE,
        CAP_QA_MANAGE,
        CAP_WEBCHAT_HANDOFF_ACCEPT, CAP_WEBCHAT_HANDOFF_DECLINE, CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
        CAP_WEBCHAT_HANDOFF_RELEASE, CAP_WEBCHAT_HANDOFF_RESUME_AI, CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
        CAP_OPERATOR_QUEUE_READ,
    },
    UserRole.lead: {
        CAP_TICKET_READ, CAP_TICKET_ASSIGN, CAP_TICKET_ESCALATE, CAP_TICKET_UPDATE_CORE,
        CAP_TICKET_STATUS_CHANGE, CAP_TICKET_CLOSE, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_ATTACHMENT_UPLOAD, CAP_CUSTOMER_PROFILE_READ,
        CAP_OUTBOUND_DRAFT_SAVE, CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE,
        CAP_NOTE_WRITE_INTERNAL, CAP_NOTE_WRITE_EXTERNAL,
        CAP_QA_MANAGE,
        CAP_WEBCHAT_HANDOFF_ACCEPT, CAP_WEBCHAT_HANDOFF_DECLINE, CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
        CAP_WEBCHAT_HANDOFF_RELEASE, CAP_WEBCHAT_HANDOFF_RESUME_AI, CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
        CAP_OPERATOR_QUEUE_READ,
    },
    UserRole.agent: {
        CAP_TICKET_READ, CAP_ATTACHMENT_READ_EXTERNAL, CAP_ATTACHMENT_READ_INTERNAL,
        CAP_ATTACHMENT_UPLOAD, CAP_TICKET_STATUS_CHANGE, CAP_OUTBOUND_DRAFT_SAVE,
        CAP_OUTBOUND_SEND, CAP_AI_INTAKE_WRITE, CAP_NOTE_WRITE_INTERNAL,
        CAP_NOTE_WRITE_EXTERNAL, CAP_CUSTOMER_PROFILE_READ,
        CAP_WEBCHAT_HANDOFF_ACCEPT, CAP_WEBCHAT_HANDOFF_DECLINE, CAP_WEBCHAT_HANDOFF_RELEASE,
        CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
        CAP_OPERATOR_QUEUE_READ,
    },
    UserRole.auditor: {
        CAP_TICKET_READ, CAP_ATTACHMENT_READ_EXTERNAL,
        CAP_ATTACHMENT_READ_INTERNAL, CAP_CUSTOMER_PROFILE_READ,
        CAP_SECURITY_READ, CAP_AUDIT_READ,
        CAP_OPERATOR_QUEUE_READ,
    },
}

_GLOBAL_CASE_VISIBILITY = frozenset({CAP_TICKET_ASSIGN, CAP_AUDIT_READ, CAP_USER_MANAGE})
_GLOBAL_ADMIN_VISIBILITY = frozenset({CAP_AUDIT_READ, CAP_USER_MANAGE})


def _base_capabilities(role: UserRole) -> set[str]:
    return set(ROLE_CAPABILITIES.get(role, set()))




def resolve_capabilities_from_preloaded(user, overrides) -> set[str]:
    """Resolve the canonical capability projection without issuing a query.

    Callers that already preloaded ``UserCapabilityOverride`` rows must use this
    public API instead of reimplementing role-default and override semantics.
    """

    capabilities = _base_capabilities(user.role)
    for override in overrides:
        if override.allowed:
            capabilities.add(override.capability)
        else:
            capabilities.discard(override.capability)
    return {capability for capability in capabilities if capability in ALL_CAPABILITIES}

def resolve_capabilities(user, db: Session | None = None) -> set[str]:
    if db is None:
        return resolve_capabilities_from_preloaded(user, ())
    overrides = db.query(UserCapabilityOverride).filter(UserCapabilityOverride.user_id == user.id).all()
    return resolve_capabilities_from_preloaded(user, overrides)


def capability_fingerprint(user, db: Session | None = None) -> str:
    """Return a bounded identity for the complete server policy projection.

    The fingerprint covers both effective capabilities and every explicit
    override decision. Unknown forward-compatible capability names are excluded
    from authorization but still invalidate cursors and cached policy views.
    """

    overrides = (
        ()
        if db is None
        else db.query(UserCapabilityOverride)
        .filter(UserCapabilityOverride.user_id == user.id)
        .all()
    )
    effective = resolve_capabilities_from_preloaded(user, overrides)
    role_value = getattr(user.role, "value", str(user.role))
    override_projection = sorted(
        f"{override.capability}={int(bool(override.allowed))}"
        for override in overrides
    )
    canonical = "\n".join(
        [
            f"role={role_value}",
            *(f"effective={capability}" for capability in sorted(effective)),
            *(f"override={decision}" for decision in override_projection),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def has_global_case_visibility(user, db: Session | None = None) -> bool:
    return bool(resolve_capabilities(user, db) & _GLOBAL_CASE_VISIBILITY)


def has_global_admin_visibility(user, db: Session | None = None) -> bool:
    return bool(resolve_capabilities(user, db) & _GLOBAL_ADMIN_VISIBILITY)


def ensure_capability(user, capability: str, db: Session | None = None, *, message: str = "Permission denied") -> None:
    if capability not in resolve_capabilities(user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def ensure_ticket_visible(user, ticket, db: Session | None = None):
    ensure_capability(user, CAP_TICKET_READ, db, message="Ticket not visible for current user")
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant authority requires a database session",
        )
    ensure_ticket_tenant_authority(db, user, ticket)
    if has_global_case_visibility(user, db):
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
    ensure_capability(user, CAP_TICKET_STATUS_CHANGE, db, message="Permission denied")
    ensure_ticket_visible(user, ticket, db)
    if new_status in {TicketStatus.closed, TicketStatus.canceled, TicketStatus.escalated}:
        ensure_capability(user, CAP_TICKET_CLOSE, db, message="Permission denied")


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


def ensure_can_view_security_audit(user, db: Session | None = None):
    capabilities = resolve_capabilities(user, db)
    if CAP_USER_MANAGE in capabilities or CAP_SECURITY_READ in capabilities or CAP_AUDIT_READ in capabilities:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view security audit")


def ensure_can_manage_channel_accounts(user, db: Session | None = None):
    ensure_capability(user, CAP_CHANNEL_ACCOUNT_MANAGE, db, message="Not authorized to manage channel accounts")


def ensure_can_manage_bulletins(user, db: Session | None = None):
    ensure_capability(user, CAP_BULLETIN_MANAGE, db, message="Not authorized to manage bulletins")


def ensure_can_read_ai_configs(user, db: Session | None = None):
    capabilities = resolve_capabilities(user, db)
    if CAP_AI_CONFIG_READ not in capabilities and CAP_AI_CONFIG_MANAGE not in capabilities:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to read AI config")


def ensure_can_manage_ai_configs(user, db: Session | None = None):
    ensure_capability(user, CAP_AI_CONFIG_MANAGE, db, message="Not authorized to manage AI config")


def ensure_can_manage_runtime(user, db: Session | None = None):
    ensure_capability(user, CAP_RUNTIME_MANAGE, db, message="Not authorized to manage runtime")


def ensure_can_manage_markets(user, db: Session | None = None):
    ensure_capability(user, CAP_MARKET_MANAGE, db, message="Not authorized to manage markets")


def ensure_can_create_speedaf_work_order(user, db: Session | None = None):
    ensure_capability(user, CAP_SPEEDAF_WORK_ORDER_WRITE, db, message="speedaf_work_order_requires_capability")


def ensure_can_update_speedaf_address(user, db: Session | None = None):
    ensure_capability(user, CAP_SPEEDAF_ADDRESS_UPDATE_WRITE, db, message="speedaf_address_update_requires_capability")


def ensure_can_cancel_speedaf_order(user, db: Session | None = None):
    ensure_capability(user, CAP_SPEEDAF_CANCEL_WRITE, db, message="speedaf_cancel_requires_capability")


def ensure_can_send_speedaf_voice_callback(user, db: Session | None = None):
    ensure_capability(user, CAP_SPEEDAF_VOICE_CALLBACK_WRITE, db, message="speedaf_voice_callback_requires_capability")


def ensure_can_read_webcall_voice(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_READ, db, message="webcall_voice_read_requires_capability")


def ensure_can_view_webcall_voice_queue(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_QUEUE_VIEW, db, message="webcall_voice_queue_requires_capability")


def ensure_can_accept_webcall_voice(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_ACCEPT, db, message="webcall_voice_accept_requires_capability")


def ensure_can_reject_webcall_voice(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_REJECT, db, message="webcall_voice_reject_requires_capability")


def ensure_can_end_webcall_voice(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_END, db, message="webcall_voice_end_requires_capability")


def ensure_can_control_webcall_voice(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCALL_VOICE_CONTROL, db, message="webcall_voice_control_requires_capability")


def ensure_can_accept_webchat_handoff(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_ACCEPT, db, message="webchat_handoff_accept_requires_capability")


def ensure_can_decline_webchat_handoff(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_DECLINE, db, message="webchat_handoff_decline_requires_capability")


def ensure_can_force_takeover_webchat(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER, db, message="webchat_handoff_force_takeover_requires_capability")


def ensure_can_release_webchat_handoff(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_RELEASE, db, message="webchat_handoff_release_requires_capability")


def ensure_can_resume_webchat_ai(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_HANDOFF_RESUME_AI, db, message="webchat_handoff_resume_ai_requires_capability")


def ensure_can_monitor_webchat_ai(user, db: Session | None = None):
    ensure_capability(user, CAP_WEBCHAT_CONVERSATION_MONITOR_AI, db, message="webchat_monitor_ai_requires_capability")
