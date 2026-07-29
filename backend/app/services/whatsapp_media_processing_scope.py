from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Tenant, Ticket
from ..models_agent_routing import ConversationControl
from ..models_whatsapp import WhatsAppMediaAsset
from .data_subject_action_service import ensure_data_processing_allowed
from .whatsapp_media_service import WhatsAppMediaError


def enforce_whatsapp_media_processing_scope(
    db: Session,
    asset: WhatsAppMediaAsset,
) -> None:
    """Resolve one inbound Customer and enforce the canonical processing purpose.

    Both Meta Worker downloads and Baileys byte callbacks enter through this
    authority before scanning, storing, or projecting customer media.
    """

    inbound = asset.inbound_message
    if inbound is None:
        raise WhatsAppMediaError("whatsapp_media_inbound_scope_missing")

    customer_id: int | None
    if inbound.ticket_id is not None:
        row = (
            db.query(Ticket.customer_id, Ticket.tenant_id)
            .filter(Ticket.id == inbound.ticket_id)
            .first()
        )
        if row is None or int(row.tenant_id or 0) != int(asset.tenant_id):
            raise WhatsAppMediaError("whatsapp_media_ticket_scope_mismatch")
        customer_id = int(row.customer_id) if row.customer_id is not None else None
    else:
        if inbound.conversation_id is None:
            raise WhatsAppMediaError("whatsapp_media_conversation_scope_missing")
        row = (
            db.query(ConversationControl.customer_id, Tenant.id)
            .join(
                Tenant,
                Tenant.tenant_key == ConversationControl.tenant_key,
            )
            .filter(
                ConversationControl.conversation_id == inbound.conversation_id,
                Tenant.is_active.is_(True),
            )
            .first()
        )
        if row is None or int(row.id) != int(asset.tenant_id):
            raise WhatsAppMediaError("whatsapp_media_conversation_scope_mismatch")
        customer_id = int(row.customer_id) if row.customer_id is not None else None

    if customer_id is not None:
        ensure_data_processing_allowed(
            db,
            customer_id=customer_id,
            purpose="human_support",
        )


__all__ = ["enforce_whatsapp_media_processing_scope"]
