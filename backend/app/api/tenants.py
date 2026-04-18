from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..tenant_schemas import (
    TenantAIProfileRead,
    TenantAIProfileUpsert,
    TenantContextRead,
    TenantCreate,
    TenantKnowledgeEntryCreate,
    TenantKnowledgeEntryRead,
    TenantKnowledgeEntryUpdate,
    TenantOptionRead,
    TenantRead,
)
from ..services.permissions import ensure_can_manage_capabilities
from ..services.tenant_service import (
    build_tenant_ai_runtime_context,
    create_tenant,
    create_tenant_knowledge_entry,
    get_or_create_tenant_ai_profile,
    list_tenant_knowledge_entries,
    list_user_tenant_options,
    resolve_current_tenant,
    tenant_summary,
    update_tenant_knowledge_entry,
    upsert_tenant_ai_profile,
)
from ..unit_of_work import managed_session
from .deps import get_current_tenant, get_current_user

router = APIRouter(prefix='/api/tenants', tags=['tenants'])


@router.get('', response_model=list[TenantOptionRead])
def list_my_tenants(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    options = list_user_tenant_options(db, current_user)
    return [TenantOptionRead(tenant=TenantRead.model_validate(item['tenant']), membership_role=item['membership_role'], is_default=item['is_default']) for item in options]


@router.post('', response_model=TenantRead)
def create_tenant_endpoint(payload: TenantCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        tenant = create_tenant(db, slug=payload.slug, name=payload.name, external_ref=payload.external_ref, owner_user_id=current_user.id)
        db.flush()
    db.refresh(tenant)
    return TenantRead.model_validate(tenant)


@router.get('/current', response_model=TenantContextRead)
def get_current_tenant_context(db: Session = Depends(get_db), current_user=Depends(get_current_user), current_tenant=Depends(get_current_tenant)):
    runtime = build_tenant_ai_runtime_context(db, current_tenant.id)
    summary = tenant_summary(db, current_tenant.id)
    membership_role = 'member'
    for item in list_user_tenant_options(db, current_user):
        if item['tenant'].id == current_tenant.id:
            membership_role = item['membership_role']
            break
    return TenantContextRead(
        tenant=TenantRead.model_validate(current_tenant),
        membership_role=membership_role,
        ai_profile=TenantAIProfileRead.model_validate(runtime['profile']),
        knowledge_entry_count=summary['knowledge_entry_count'],
    )


@router.get('/current/ai-profile', response_model=TenantAIProfileRead)
def get_current_tenant_ai_profile(db: Session = Depends(get_db), current_tenant=Depends(get_current_tenant)):
    row = get_or_create_tenant_ai_profile(db, current_tenant.id)
    return TenantAIProfileRead.model_validate(row)


@router.put('/current/ai-profile', response_model=TenantAIProfileRead)
def update_current_tenant_ai_profile(payload: TenantAIProfileUpsert, db: Session = Depends(get_db), current_user=Depends(get_current_user), current_tenant=Depends(get_current_tenant)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = upsert_tenant_ai_profile(db, current_tenant.id, payload.model_dump())
        db.flush()
    db.refresh(row)
    return TenantAIProfileRead.model_validate(row)


@router.get('/current/knowledge', response_model=list[TenantKnowledgeEntryRead])
def list_current_tenant_knowledge(active_only: bool = False, db: Session = Depends(get_db), current_tenant=Depends(get_current_tenant)):
    rows = list_tenant_knowledge_entries(db, current_tenant.id, active_only=active_only)
    return [TenantKnowledgeEntryRead.model_validate(row) for row in rows]


@router.post('/current/knowledge', response_model=TenantKnowledgeEntryRead)
def create_current_tenant_knowledge(payload: TenantKnowledgeEntryCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user), current_tenant=Depends(get_current_tenant)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = create_tenant_knowledge_entry(db, current_tenant.id, payload.model_dump())
        db.flush()
    db.refresh(row)
    return TenantKnowledgeEntryRead.model_validate(row)


@router.patch('/current/knowledge/{entry_id}', response_model=TenantKnowledgeEntryRead)
def update_current_tenant_knowledge(entry_id: int, payload: TenantKnowledgeEntryUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user), current_tenant=Depends(get_current_tenant)):
    ensure_can_manage_capabilities(current_user, db)
    with managed_session(db):
        row = update_tenant_knowledge_entry(db, current_tenant.id, entry_id, payload.model_dump(exclude_unset=True))
        db.flush()
    db.refresh(row)
    return TenantKnowledgeEntryRead.model_validate(row)
