from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..models import AIConfigResource, Tenant
from ..models_agent_control import AgentResourceBinding
from ..models_control_plane import PersonaProfile

SESSION_ACTOR_KEY = "nexus_authenticated_user"
SESSION_RESOURCE_SCOPE_KEY = "nexus_agent_resource_tenant"
_SKIP_SCOPE_OPTION = "nexus_skip_agent_resource_scope"
PERSONA_RESOURCE = "persona"
AI_CONFIG_RESOURCE = "ai_config"
RESOURCE_TYPES = {PERSONA_RESOURCE, AI_CONFIG_RESOURCE}


def bind_session_actor(db: Session, actor: Any) -> None:
    db.info[SESSION_ACTOR_KEY] = actor
    tenant_id = getattr(actor, "tenant_id", None)
    if tenant_id is None:
        db.info.pop(SESSION_RESOURCE_SCOPE_KEY, None)
        return
    tenant = db.get(
        Tenant,
        int(tenant_id),
        execution_options={_SKIP_SCOPE_OPTION: True},
    )
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")
    set_session_resource_scope(db, tenant.tenant_key)


def set_session_resource_scope(db: Session, tenant_key: str | None) -> None:
    cleaned = str(tenant_key or "").strip().lower()
    if not cleaned:
        db.info.pop(SESSION_RESOURCE_SCOPE_KEY, None)
        return
    if len(cleaned) > 80:
        raise HTTPException(status_code=400, detail="tenant_key_invalid")
    db.info[SESSION_RESOURCE_SCOPE_KEY] = cleaned


def session_actor(db: Session) -> Any | None:
    return db.info.get(SESSION_ACTOR_KEY)


def session_resource_scope(db: Session) -> str | None:
    value = db.info.get(SESSION_RESOURCE_SCOPE_KEY)
    return str(value).strip().lower() if value else None


def actor_tenant_key(
    db: Session,
    actor: Any | None = None,
    *,
    platform_default: str = "default",
) -> str:
    actor = actor or session_actor(db)
    if actor is None:
        raise HTTPException(status_code=403, detail="agent_resource_actor_required")
    tenant_id = getattr(actor, "tenant_id", None)
    if tenant_id is None:
        return platform_default
    scoped = session_resource_scope(db)
    if scoped:
        return scoped
    tenant = db.get(
        Tenant,
        int(tenant_id),
        execution_options={_SKIP_SCOPE_OPTION: True},
    )
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")
    key = str(tenant.tenant_key).strip().lower()
    set_session_resource_scope(db, key)
    return key


def bind_resource(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
    tenant_key: str,
    actor_id: int | None,
    is_global_template: bool = False,
) -> AgentResourceBinding:
    if resource_type not in RESOURCE_TYPES:
        raise ValueError("agent_resource_type_invalid")
    existing = resource_binding(db, resource_type=resource_type, resource_id=resource_id)
    if existing is not None:
        if (
            existing.tenant_key != tenant_key
            or bool(existing.is_global_template) != bool(is_global_template)
        ):
            raise HTTPException(status_code=409, detail="agent_resource_binding_conflict")
        return existing
    row = AgentResourceBinding(
        tenant_key=str(tenant_key).strip().lower(),
        resource_type=resource_type,
        resource_id=int(resource_id),
        is_global_template=bool(is_global_template),
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def resource_binding(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
) -> AgentResourceBinding | None:
    return (
        db.query(AgentResourceBinding)
        .filter(
            AgentResourceBinding.resource_type == resource_type,
            AgentResourceBinding.resource_id == int(resource_id),
        )
        .one_or_none()
    )


def visible_resource_ids(
    db: Session,
    *,
    resource_type: str,
    actor: Any | None = None,
    include_global_templates: bool = True,
) -> set[int] | None:
    """Return exact IDs, or None only for an unscoped platform actor."""

    actor = actor or session_actor(db)
    if actor is None:
        return set()
    tenant_key = session_resource_scope(db)
    if getattr(actor, "tenant_id", None) is None and tenant_key is None:
        return None
    tenant_key = tenant_key or actor_tenant_key(db, actor)
    query = db.query(AgentResourceBinding.resource_id).filter(
        AgentResourceBinding.resource_type == resource_type
    )
    if include_global_templates:
        query = query.filter(
            (AgentResourceBinding.tenant_key == tenant_key)
            | (AgentResourceBinding.is_global_template.is_(True))
        )
    else:
        query = query.filter(
            AgentResourceBinding.tenant_key == tenant_key,
            AgentResourceBinding.is_global_template.is_(False),
        )
    return {int(row[0]) for row in query.all()}


def manageable_resource_ids(
    db: Session,
    *,
    resource_type: str,
    actor: Any | None = None,
) -> set[int] | None:
    return visible_resource_ids(
        db,
        resource_type=resource_type,
        actor=actor,
        include_global_templates=False,
    )


def ensure_resource_visible(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
    actor: Any | None = None,
) -> AgentResourceBinding | None:
    actor = actor or session_actor(db)
    if (
        actor is not None
        and getattr(actor, "tenant_id", None) is None
        and session_resource_scope(db) is None
    ):
        return resource_binding(db, resource_type=resource_type, resource_id=resource_id)
    allowed = visible_resource_ids(
        db,
        resource_type=resource_type,
        actor=actor,
        include_global_templates=True,
    )
    if int(resource_id) not in (allowed or set()):
        raise HTTPException(status_code=404, detail="agent_resource_not_found")
    return resource_binding(db, resource_type=resource_type, resource_id=resource_id)


def ensure_resource_manageable(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
    actor: Any | None = None,
) -> AgentResourceBinding | None:
    actor = actor or session_actor(db)
    if (
        actor is not None
        and getattr(actor, "tenant_id", None) is None
        and session_resource_scope(db) is None
    ):
        return resource_binding(db, resource_type=resource_type, resource_id=resource_id)
    allowed = manageable_resource_ids(db, resource_type=resource_type, actor=actor)
    if int(resource_id) not in (allowed or set()):
        raise HTTPException(status_code=404, detail="agent_resource_not_found")
    return resource_binding(db, resource_type=resource_type, resource_id=resource_id)


def ensure_resource_releasable(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
    tenant_key: str,
) -> AgentResourceBinding:
    binding = resource_binding(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if binding is None:
        raise HTTPException(status_code=409, detail="agent_resource_binding_missing")
    if binding.is_global_template or binding.tenant_key == tenant_key:
        return binding
    raise HTTPException(status_code=403, detail="cross_tenant_agent_resource_forbidden")


def resource_access_projection(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
    actor: Any | None = None,
) -> dict[str, Any]:
    binding = resource_binding(db, resource_type=resource_type, resource_id=resource_id)
    actor = actor or session_actor(db)
    platform_unscoped = (
        actor is not None
        and getattr(actor, "tenant_id", None) is None
        and session_resource_scope(db) is None
    )
    manageable = platform_unscoped
    if binding is not None and actor is not None and not platform_unscoped:
        manageable = (
            binding.tenant_key == (session_resource_scope(db) or actor_tenant_key(db, actor))
            and not binding.is_global_template
        )
    return {
        "tenant_key": binding.tenant_key if binding else None,
        "is_global_template": bool(binding.is_global_template) if binding else False,
        "can_manage": bool(manageable),
    }


@event.listens_for(Session, "do_orm_execute")
def _apply_agent_resource_scope(execute_state) -> None:  # noqa: ANN001
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get(_SKIP_SCOPE_OPTION):
        return
    tenant_key = session_resource_scope(execute_state.session)
    if not tenant_key:
        return
    persona_ids = select(AgentResourceBinding.resource_id).where(
        AgentResourceBinding.resource_type == PERSONA_RESOURCE,
        or_(
            AgentResourceBinding.tenant_key == tenant_key,
            AgentResourceBinding.is_global_template.is_(True),
        ),
    )
    config_ids = select(AgentResourceBinding.resource_id).where(
        AgentResourceBinding.resource_type == AI_CONFIG_RESOURCE,
        or_(
            AgentResourceBinding.tenant_key == tenant_key,
            AgentResourceBinding.is_global_template.is_(True),
        ),
    )
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            PersonaProfile,
            PersonaProfile.id.in_(persona_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            AIConfigResource,
            AIConfigResource.id.in_(config_ids),
            include_aliases=True,
        ),
    )
