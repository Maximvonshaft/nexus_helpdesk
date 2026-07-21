from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AIConfigResource, KnowledgeItem, Tenant
from ..models_agent_control import (
    AgentDefinition,
    AgentDeployment,
    AgentRelease,
    AgentRunSnapshot,
)
from ..models_control_plane import PersonaProfile
from ..utils.time import utc_now
from .agent_control_config import (
    INTEGRATION,
    MODEL_PROFILE,
    PLAYBOOK,
    RUNTIME_POLICY,
    validate_agent_config_content,
)

RELEASE_SCHEMA = "nexus.agent_release.v1"
_ALLOWED_RESOURCE_SECTIONS = {
    "playbooks": PLAYBOOK,
    "integrations": INTEGRATION,
    "model_profile": MODEL_PROFILE,
    "runtime_policy": RUNTIME_POLICY,
}


@dataclass(frozen=True)
class ResolvedAgentRelease:
    deployment: AgentDeployment | None
    release: AgentRelease | None
    snapshot: dict[str, Any]
    digest: str
    source: str


def authoritative_tenant_key(
    db: Session,
    user: Any,
    *,
    requested: str | None = None,
    allow_platform_default: bool = False,
) -> str:
    """Resolve tenant from authenticated identity, never from an untrusted body.

    Tenant-bound users may only operate on their own tenant. Platform users
    without a tenant assignment must state an explicit tenant; ``default`` is
    reserved for the platform fallback deployment and is allowed only where the
    caller explicitly opts into it.
    """

    tenant_id = getattr(user, "tenant_id", None)
    if tenant_id is not None:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")
        tenant_key = str(tenant.tenant_key).strip().lower()
        if requested and requested.strip().lower() != tenant_key:
            raise HTTPException(status_code=403, detail="cross_tenant_agent_control_forbidden")
        return tenant_key

    tenant_key = str(requested or "").strip().lower()
    if not tenant_key:
        raise HTTPException(status_code=400, detail="tenant_key_required_for_platform_actor")
    if tenant_key == "default":
        if allow_platform_default:
            return tenant_key
        raise HTTPException(status_code=400, detail="default_tenant_not_allowed")
    tenant = db.query(Tenant).filter(Tenant.tenant_key == tenant_key, Tenant.is_active.is_(True)).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    return tenant_key


def canonical_scope_key(
    *,
    market_id: int | None,
    channel: str | None,
    language: str | None,
    case_type: str | None,
) -> str:
    values = (
        str(market_id) if market_id is not None else "*",
        _scope_value(channel),
        _scope_value(language),
        _scope_value(case_type),
    )
    return "|".join(values)


def validate_release_manifest(db: Session, manifest: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="agent_release_manifest_must_be_object")
    if manifest.get("schema_version") != RELEASE_SCHEMA:
        raise HTTPException(status_code=400, detail="agent_release_schema_not_supported")

    normalized: dict[str, Any] = {"schema_version": RELEASE_SCHEMA}
    evidence: dict[str, Any] = {"resources": [], "knowledge": [], "persona": None}

    persona_ref = manifest.get("persona")
    if persona_ref not in (None, {}):
        if not isinstance(persona_ref, dict):
            raise HTTPException(status_code=400, detail="agent_release_persona_reference_invalid")
        key = _required_key(persona_ref.get("profile_key"), "persona_profile_key")
        version = _positive_int(persona_ref.get("version"), "persona_version")
        row = (
            db.query(PersonaProfile)
            .filter(
                PersonaProfile.profile_key == key,
                PersonaProfile.is_active.is_(True),
                PersonaProfile.published_version == version,
            )
            .one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=409, detail="persona_release_reference_not_found")
        normalized["persona"] = {"profile_key": key, "version": version}
        evidence["persona"] = {"id": row.id, "profile_key": key, "version": version}
    else:
        normalized["persona"] = None

    for section, config_type in _ALLOWED_RESOURCE_SECTIONS.items():
        raw = manifest.get(section)
        refs = raw if isinstance(raw, list) else ([] if raw in (None, {}) else [raw])
        if section in {"model_profile", "runtime_policy"} and len(refs) != 1:
            raise HTTPException(status_code=400, detail=f"agent_release_{section}_requires_one_reference")
        resolved_refs: list[dict[str, Any]] = []
        for item in refs:
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail=f"agent_release_{section}_reference_invalid")
            key = _required_key(item.get("resource_key"), f"{section}_resource_key")
            version = _positive_int(item.get("version"), f"{section}_version")
            row = (
                db.query(AIConfigResource)
                .filter(
                    AIConfigResource.resource_key == key,
                    AIConfigResource.config_type == config_type,
                    AIConfigResource.is_active.is_(True),
                    AIConfigResource.published_version == version,
                )
                .one_or_none()
            )
            if row is None:
                raise HTTPException(status_code=409, detail=f"agent_release_{section}_reference_not_found")
            content = validate_agent_config_content(config_type, row.published_content_json or {})
            resolved_refs.append({"resource_key": key, "version": version})
            evidence["resources"].append(
                {"id": row.id, "resource_key": key, "config_type": config_type, "version": version, "content": content}
            )
        normalized[section] = resolved_refs if section not in {"model_profile", "runtime_policy"} else resolved_refs[0]

    knowledge_refs: list[dict[str, Any]] = []
    for item in manifest.get("knowledge", []) or []:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="agent_release_knowledge_reference_invalid")
        key = _required_key(item.get("item_key"), "knowledge_item_key")
        version = _positive_int(item.get("version"), "knowledge_version")
        row = (
            db.query(KnowledgeItem)
            .filter(
                KnowledgeItem.item_key == key,
                KnowledgeItem.published_version == version,
                KnowledgeItem.status == "published",
            )
            .one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=409, detail="agent_release_knowledge_reference_not_found")
        knowledge_refs.append({"item_key": key, "version": version})
        evidence["knowledge"].append({"id": row.id, "item_key": key, "version": version})
    normalized["knowledge"] = knowledge_refs

    normalized["metadata"] = _bounded_metadata(manifest.get("metadata"))
    return normalized, evidence


def create_release(
    db: Session,
    *,
    definition: AgentDefinition,
    actor_id: int | None,
) -> AgentRelease:
    normalized, evidence = validate_release_manifest(db, definition.draft_manifest_json)
    latest = (
        db.query(AgentRelease)
        .filter(AgentRelease.definition_id == definition.id)
        .order_by(AgentRelease.version.desc())
        .first()
    )
    version = int(latest.version if latest else 0) + 1
    digest = manifest_digest(normalized)
    row = AgentRelease(
        definition_id=definition.id,
        version=version,
        status="approved",
        manifest_json=normalized,
        manifest_sha256=digest,
        validation_json=evidence,
        created_by=actor_id,
        approved_by=actor_id,
        approved_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def activate_deployment(
    db: Session,
    *,
    tenant_key: str,
    environment: str,
    release: AgentRelease,
    actor_id: int | None,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    case_type: str | None = None,
    canary_release: AgentRelease | None = None,
    canary_percent: int = 0,
) -> AgentDeployment:
    if environment not in {"test", "staging", "production"}:
        raise HTTPException(status_code=400, detail="agent_environment_invalid")
    if canary_percent < 0 or canary_percent > 100:
        raise HTTPException(status_code=400, detail="agent_canary_percent_invalid")
    definition = db.get(AgentDefinition, release.definition_id)
    if definition is None or definition.tenant_key != tenant_key:
        raise HTTPException(status_code=409, detail="agent_release_tenant_mismatch")
    if canary_release is not None and canary_release.definition_id != release.definition_id:
        raise HTTPException(status_code=409, detail="agent_canary_definition_mismatch")

    scope_key = canonical_scope_key(
        market_id=market_id, channel=channel, language=language, case_type=case_type
    )
    row = (
        db.query(AgentDeployment)
        .filter(
            AgentDeployment.tenant_key == tenant_key,
            AgentDeployment.environment == environment,
            AgentDeployment.scope_key == scope_key,
        )
        .one_or_none()
    )
    if row is None:
        row = AgentDeployment(
            tenant_key=tenant_key,
            environment=environment,
            scope_key=scope_key,
            market_id=market_id,
            channel=_optional_scope(channel),
            language=_optional_scope(language),
            case_type=_optional_scope(case_type),
            active_release_id=release.id,
        )
        db.add(row)
    row.active_release_id = release.id
    row.canary_release_id = canary_release.id if canary_release else None
    row.canary_percent = canary_percent if canary_release else 0
    row.is_active = True
    row.activated_by = actor_id
    row.activated_at = utc_now()
    release.status = "active"
    if canary_release is not None:
        canary_release.status = "canary"
    db.flush()
    return row


def resolve_agent_release(
    db: Session,
    *,
    tenant_key: str,
    environment: str = "production",
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
    case_type: str | None = None,
    cohort_key: str,
) -> ResolvedAgentRelease:
    rows = (
        db.query(AgentDeployment)
        .filter(
            AgentDeployment.tenant_key == tenant_key,
            AgentDeployment.environment == environment,
            AgentDeployment.is_active.is_(True),
        )
        .all()
    )
    matches: list[tuple[int, AgentDeployment]] = []
    for row in rows:
        rank = _deployment_rank(
            row,
            market_id=market_id,
            channel=channel,
            language=language,
            case_type=case_type,
        )
        if rank >= 0:
            matches.append((rank, row))
    if not matches:
        snapshot = {"schema_version": RELEASE_SCHEMA, "source": "builtin", "tenant_key": tenant_key}
        return ResolvedAgentRelease(None, None, snapshot, manifest_digest(snapshot), "builtin")
    matches.sort(key=lambda item: (-item[0], item[1].id))
    best_rank = matches[0][0]
    tied = [item for item in matches if item[0] == best_rank]
    if len(tied) != 1:
        raise RuntimeError("ambiguous_agent_deployment_scope")
    deployment = tied[0][1]
    release_id = deployment.active_release_id
    cohort = _cohort_percent(cohort_key)
    if (
        deployment.canary_release_id is not None
        and deployment.canary_percent > 0
        and cohort < deployment.canary_percent
    ):
        release_id = deployment.canary_release_id
    release = db.get(AgentRelease, release_id)
    if release is None or release.status not in {"approved", "canary", "active"}:
        raise RuntimeError("agent_deployment_release_unavailable")
    definition = db.get(AgentDefinition, release.definition_id)
    if definition is None or definition.tenant_key != tenant_key or not definition.is_active:
        raise RuntimeError("agent_definition_unavailable")
    snapshot = {
        "schema_version": RELEASE_SCHEMA,
        "source": "deployment",
        "tenant_key": tenant_key,
        "definition": {
            "id": definition.id,
            "definition_key": definition.definition_key,
            "name": definition.name,
        },
        "deployment": {
            "id": deployment.id,
            "environment": deployment.environment,
            "scope_key": deployment.scope_key,
            "cohort": cohort,
            "canary": release.id == deployment.canary_release_id,
        },
        "release": {
            "id": release.id,
            "version": release.version,
            "manifest_sha256": release.manifest_sha256,
        },
        "manifest": release.manifest_json,
    }
    digest = manifest_digest(snapshot)
    return ResolvedAgentRelease(deployment, release, snapshot, digest, "deployment")


def record_run_snapshot(
    db: Session,
    *,
    request_id: str,
    session_id: str,
    tenant_key: str,
    resolved: ResolvedAgentRelease,
) -> AgentRunSnapshot:
    existing = (
        db.query(AgentRunSnapshot)
        .filter(AgentRunSnapshot.request_id == request_id)
        .one_or_none()
    )
    if existing is not None:
        if existing.snapshot_sha256 != resolved.digest:
            raise RuntimeError("agent_run_snapshot_idempotency_conflict")
        return existing
    row = AgentRunSnapshot(
        request_id=request_id[:160],
        session_id=session_id[:160],
        tenant_key=tenant_key[:80],
        deployment_id=resolved.deployment.id if resolved.deployment else None,
        release_id=resolved.release.id if resolved.release else None,
        snapshot_sha256=resolved.digest,
        snapshot_json=resolved.snapshot,
        source=resolved.source,
    )
    db.add(row)
    db.flush()
    return row


def manifest_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _deployment_rank(
    row: AgentDeployment,
    *,
    market_id: int | None,
    channel: str | None,
    language: str | None,
    case_type: str | None,
) -> int:
    rank = 0
    for expected, actual, weight in (
        (row.market_id, market_id, 16),
        (_optional_scope(row.channel), _optional_scope(channel), 8),
        (_optional_scope(row.language), _optional_scope(language), 4),
        (_optional_scope(row.case_type), _optional_scope(case_type), 2),
    ):
        if expected is None:
            continue
        if expected != actual:
            return -1
        rank += weight
    return rank


def _cohort_percent(value: str) -> int:
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


def _scope_value(value: str | None) -> str:
    return _optional_scope(value) or "*"


def _optional_scope(value: Any) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _required_key(value: Any, label: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned or len(cleaned) > 160:
        raise HTTPException(status_code=400, detail=f"{label}_invalid")
    return cleaned


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{label}_invalid") from exc
    if parsed <= 0:
        raise HTTPException(status_code=400, detail=f"{label}_invalid")
    return parsed


def _bounded_metadata(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="agent_release_metadata_invalid")
    output: dict[str, Any] = {}
    for key, item in list(value.items())[:30]:
        normalized_key = str(key).strip()[:80]
        if not normalized_key:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            output[normalized_key] = item if not isinstance(item, str) else item[:500]
    return output
