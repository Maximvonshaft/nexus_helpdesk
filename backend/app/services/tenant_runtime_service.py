from __future__ import annotations

from sqlalchemy.orm import Session

from .tenant_service import build_tenant_ai_runtime_context, tenant_id_for_ticket


def get_ticket_tenant_runtime_context(db: Session, ticket_id: int) -> dict:
    tenant_id = tenant_id_for_ticket(db, ticket_id)
    if tenant_id is None:
        return {'tenant_id': None, 'profile': None, 'knowledge_context': ''}
    runtime = build_tenant_ai_runtime_context(db, tenant_id)
    profile = runtime.get('profile')
    entries = runtime.get('knowledge_entries') or []
    knowledge_context = '\n\n'.join(
        f"[{entry.category}] {entry.title}: {entry.content}" for entry in entries if getattr(entry, 'is_active', True)
    )
    return {
        'tenant_id': tenant_id,
        'profile': profile,
        'knowledge_context': knowledge_context,
    }
