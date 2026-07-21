from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AIConfigResource, AIConfigVersion, Tenant
from ..models_agent_control import AgentDefinition, AgentDeployment, AgentRelease, AgentRunSnapshot
from ..models_control_plane import KnowledgeItem, KnowledgeItemVersion, PersonaProfile, PersonaProfileVersion
from ..utils.time import utc_now
from .agent_control_config import INTEGRATION, MODEL_PROFILE, PLAYBOOK, RUNTIME_POLICY, validate_agent_config_content

RELEASE_SCHEMA = "nexus.agent_release.v1"
_SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,159}$")
_RESOURCE_SECTIONS = {
    "playbooks": (PLAYBOOK, True),
    "integrations": (INTEGRATION, True),
    "model_profile": (MODEL_PROFILE, False),
    "runtime_policy": (RUNTIME_POLICY, False),
}
_ALLOWED_MANIFEST_KEYS = {
    "schema_version", "persona", "playbooks", "integrations",
    "model_profile", "runtime_policy", "knowledge", "metadata",
}


class AgentDeploymentUnavailable(RuntimeError):
    """No unique approved deployment exists for the requested runtime scope."""


@dataclass(frozen=True)
class ResolvedAgentRelease:
    deployment: AgentDeployment
    release: AgentRelease
    snapshot: dict[str, Any]
    digest: str
    source: str = "deployment"


def authoritative_tenant_key(
    db: Session,
    user: Any,
    *,
    requested: str | None = None,
    allow_platform_default: bool = False,
) -> str:
    """Resolve tenant from authenticated identity, never request data alone."""

    tenant_id = getattr(user, "tenant_id", None)
    if tenant_id is not None:
        tenant = db.get(Tenant, int(tenant_id))
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")
        tenant_key = _tenant_key(tenant.tenant_key)
        if requested and _tenant_key(requested) != tenant_key:
            raise HTTPException(status_code=403, detail="cross_tenant_agent_control_forbidden")
        return tenant_key

    requested_key = _tenant_key(requested) if requested else None
    if requested_key == "default":
        if allow_platform_default:
            return "default"
        raise HTTPException(status_code=400, detail="default_tenant_not_allowed")
    if requested_key is None:
        if allow_platform_default:
            return "default"
        raise HTTPException(status_code=400, detail="tenant_key_required_for_platform_actor")
    tenant = (
        db.query(Tenant)
        .filter(Tenant.tenant_key == requested_key, Tenant.is_active.is_(True))
        .one_or_none()
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    return requested_key


def canonical_scope_key(
    *, market_id: int | None, channel: str | None,
    language: str | None, case_type: str | None,
) -> str:
    return "|".join(
        (
            f"market:{market_id if market_id is not None else '*'}",
            f"channel:{_optional_scope(channel, 40) or '*'}",
            f"language:{_optional_scope(language, 24) or '*'}",
            f"case:{_optional_scope(case_type, 80) or '*'}",
        )
    )


def validate_release_manifest(
    db: Session,
    manifest: Any,
    *,
    tenant_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="agent_release_manifest_must_be_object")
    unknown = sorted(set(manifest) - _ALLOWED_MANIFEST_KEYS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "agent_release_manifest_unknown_fields", "fields": unknown},
        )
    if str(manifest.get("schema_version") or RELEASE_SCHEMA) != RELEASE_SCHEMA:
        raise HTTPException(status_code=400, detail="agent_release_schema_not_supported")

    normalized: dict[str, Any] = {
        "schema_version": RELEASE_SCHEMA,
        "persona": None,
        "playbooks": [],
        "integrations": [],
        "model_profile": None,
        "runtime_policy": None,
        "knowledge": [],
        "metadata": _bounded_metadata(manifest.get("metadata")),
    }
    evidence: dict[str, Any] = {
        "schema_version": RELEASE_SCHEMA,
        "tenant_key": tenant_key,
        "resources": [],
        "knowledge": [],
        "persona": None,
    }

    persona_ref = manifest.get("persona")
    if persona_ref not in (None, {}):
        ref = _version_reference(persona_ref, "persona")
        profile = (
            db.query(PersonaProfile)
            .filter(PersonaProfile.profile_key == ref["key"], PersonaProfile.is_active.is_(True))
            .one_or_none()
        )
        if profile is None:
            raise HTTPException(status_code=409, detail="persona_release_reference_not_found")
        version_row = (
            db.query(PersonaProfileVersion)
            .filter(
                PersonaProfileVersion.profile_id == profile.id,
                PersonaProfileVersion.version == ref["version"],
            )
            .one_or_none()
        )
        if version_row is None:
            raise HTTPException(status_code=409, detail="persona_release_version_not_found")
        normalized["persona"] = {"profile_key": profile.profile_key, "version": version_row.version}
        evidence["persona"] = {
            "id": profile.id,
            "profile_key": profile.profile_key,
            "version": version_row.version,
            "snapshot": _json_object(version_row.snapshot_json),
        }

    seen_resources: set[tuple[str, str, int]] = set()
    for section, (config_type, many) in _RESOURCE_SECTIONS.items():
        raw = manifest.get(section)
        refs = raw if isinstance(raw, list) else ([] if raw in (None, {}) else [raw])
        if not many and len(refs) != 1:
            raise HTTPException(status_code=400, detail=f"agent_release_{section}_requires_one_reference")
        resolved_refs: list[dict[str, Any]] = []
        for raw_ref in refs:
            ref = _version_reference(raw_ref, section)
            resource = (
                db.query(AIConfigResource)
                .filter(
                    AIConfigResource.resource_key == ref["key"],
                    AIConfigResource.config_type == config_type,
                    AIConfigResource.is_active.is_(True),
                )
                .one_or_none()
            )
            if resource is None:
                raise HTTPException(status_code=409, detail=f"agent_release_{section}_reference_not_found")
            version_row = (
                db.query(AIConfigVersion)
                .filter(
                    AIConfigVersion.resource_id == resource.id,
                    AIConfigVersion.version == ref["version"],
                )
                .one_or_none()
            )
            if version_row is None:
                raise HTTPException(status_code=409, detail=f"agent_release_{section}_version_not_found")
            identity = (config_type, resource.resource_key, version_row.version)
            if identity in seen_resources:
                raise HTTPException(status_code=400, detail="agent_release_duplicate_resource")
            seen_resources.add(identity)
            content = validate_agent_config_content(config_type, version_row.snapshot_json or {})
            resolved_refs.append({"resource_key": resource.resource_key, "version": version_row.version})
            evidence["resources"].append(
                {
                    "id": resource.id,
                    "resource_key": resource.resource_key,
                    "config_type": config_type,
                    "version": version_row.version,
                    "scope": {
                        "scope_type": resource.scope_type,
                        "scope_value": resource.scope_value,
                        "market_id": resource.market_id,
                    },
                    "content": content,
                }
            )
        normalized[section] = resolved_refs if many else resolved_refs[0]

    seen_knowledge: set[tuple[str, int]] = set()
    raw_knowledge = manifest.get("knowledge") or []
    if not isinstance(raw_knowledge, list):
        raise HTTPException(status_code=400, detail="agent_release_knowledge_must_be_list")
    for raw_ref in raw_knowledge:
        ref = _version_reference(raw_ref, "knowledge")
        knowledge = db.query(KnowledgeItem).filter(KnowledgeItem.item_key == ref["key"]).one_or_none()
        if knowledge is None or knowledge.status != "active":
            raise HTTPException(status_code=409, detail="agent_release_knowledge_reference_not_found")
        if str(knowledge.tenant_id or "default") not in {tenant_key, "default"}:
            raise HTTPException(status_code=403, detail="cross_tenant_agent_knowledge_forbidden")
        version_row = (
            db.query(KnowledgeItemVersion)
            .filter(
                KnowledgeItemVersion.item_id == knowledge.id,
                KnowledgeItemVersion.version == ref["version"],
            )
            .one_or_none()
        )
        if version_row is None:
            raise HTTPException(status_code=409, detail="agent_release_knowledge_version_not_found")
        identity = (knowledge.item_key, version_row.version)
        if identity in seen_knowledge:
            raise HTTPException(status_code=400, detail="agent_release_duplicate_knowledge")
        seen_knowledge.add(identity)
        normalized["knowledge"].append({"item_key": knowledge.item_key, "version": version_row.version})
        evidence["knowledge"].append(
            {
                "id": knowledge.id,
                "item_key": knowledge.item_key,
                "version": version_row.version,
                "snapshot": _json_object(version_row.snapshot_json),
            }
        )

    if not normalized["playbooks"]:
        raise HTTPException(status_code=400, detail="agent_release_playbook_required")
    allowed_tools = {
        str(tool)
        for row in evidence["resources"]
        if row["config_type"] == PLAYBOOK
        for tool in row["content"].get("tools") or []
    }
    if normalized["knowledge"] and "knowledge.search" not in allowed_tools:
        raise HTTPException(status_code=400, detail="agent_release_knowledge_requires_search_tool")
    if normalized["integrations"] and not ({"integration.read", "integration.write"} & allowed_tools):
        raise HTTPException(status_code=400, detail="agent_release_integration_requires_tool")
    evidence["allowed_tools"] = sorted(allowed_tools)
    evidence["manifest_sha256"] = manifest_digest(normalized)
    return normalized, evidence


def create_release(
    db: Session,
    *, definition: AgentDefinition,
    actor_id: int | None,
) -> AgentRelease:
    if not definition.is_active:
        raise HTTPException(status_code=409, detail="agent_definition_inactive")
    normalized, evidence = validate_release_manifest(
        db,
        definition.draft_manifest_json,
        tenant_key=definition.tenant_key,
    )
    latest = (
        db.query(AgentRelease.version)
        .filter(AgentRelease.definition_id == definition.id)
        .order_by(AgentRelease.version.desc())
        .first()
    )
    version = int(latest[0] if latest else 0) + 1
    now = utc_now()
    row = AgentRelease(
        definition_id=definition.id,
        version=version,
        status="approved",
        manifest_json=normalized,
        manifest_sha256=manifest_digest(normalized),
        validation_json=evidence,
        created_by=actor_id,
        approved_by=actor_id,
        created_at=now,
        approved_at=now,
    )
    db.add(row)
    db.flush()
    return row


def activate_deployment(
    db: Session,
    *, tenant_key: str, environment: str,
    release: AgentRelease, actor_id: int | None,
    market_id: int | None = None, channel: str | None = None,
    language: str | None = None, case_type: str | None = None,
    canary_release: AgentRelease | None = None, canary_percent: int = 0,
) -> AgentDeployment:
    environment = str(environment or "production").strip().lower()
    if environment not in {"test", "staging", "production"}:
        raise HTTPException(status_code=400, detail="agent_environment_invalid")
    if canary_percent < 0 or canary_percent > 100:
        raise HTTPException(status_code=400, detail="agent_canary_percent_invalid")
    if (canary_release is None) != (canary_percent == 0):
        raise HTTPException(status_code=400, detail="agent_canary_release_and_percent_must_match")
    if canary_release is not None and canary_release.id == release.id:
        raise HTTPException(status_code=400, detail="agent_canary_release_must_differ")
    _assert_release_tenant(db, release, tenant_key)
    if release.status != "approved":
        raise HTTPException(status_code=409, detail="agent_release_not_approved")
    if canary_release is not None:
        _assert_release_tenant(db, canary_release, tenant_key)
        if canary_release.status != "approved":
            raise HTTPException(status_code=409, detail="agent_canary_release_not_approved")

    channel = _optional_scope(channel, 40)
    language = _optional_scope(language, 24)
    case_type = _optional_scope(case_type, 80)
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
            channel=channel,
            language=language,
            case_type=case_type,
            active_release_id=release.id,
        )
        db.add(row)
    row.active_release_id = release.id
    row.canary_release_id = canary_release.id if canary_release else None
    row.canary_percent = canary_percent if canary_release else 0
    row.is_active = True
    row.activated_by = actor_id
    row.activated_at = utc_now()
    db.flush()
    return row


def resolve_agent_release(
    db: Session,
    *, tenant_key: str, environment: str = "production",
    market_id: int | None = None, channel: str | None = None,
    language: str | None = None, case_type: str | None = None,
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
    matches = [
        (rank, row)
        for row in rows
        if (rank := _deployment_rank(
            row, market_id=market_id, channel=channel,
            language=language, case_type=case_type
        )) >= 0
    ]
    if not matches:
        raise AgentDeploymentUnavailable("agent_deployment_not_found")
    matches.sort(key=lambda item: (-item[0], item[1].id))
    best_rank = matches[0][0]
    tied = [row for rank, row in matches if rank == best_rank]
    if len(tied) != 1:
        raise AgentDeploymentUnavailable("ambiguous_agent_deployment_scope")
    deployment = tied[0]
    cohort = _cohort_percent(cohort_key)
    release_id = deployment.active_release_id
    is_canary = False
    if (
        deployment.canary_release_id is not None
        and deployment.canary_percent > 0
        and cohort < deployment.canary_percent
    ):
        release_id = deployment.canary_release_id
        is_canary = True
    release = db.get(AgentRelease, release_id)
    if release is None:
        raise AgentDeploymentUnavailable("agent_deployment_release_unavailable")
    _assert_release_tenant(db, release, tenant_key)
    if release.status != "approved":
        raise AgentDeploymentUnavailable("agent_deployment_release_retired")
    if manifest_digest(release.manifest_json) != release.manifest_sha256:
        raise AgentDeploymentUnavailable("agent_release_digest_mismatch")
    validation = _json_object(release.validation_json)
    if validation.get("manifest_sha256") != release.manifest_sha256:
        raise AgentDeploymentUnavailable("agent_release_validation_mismatch")
    definition = db.get(AgentDefinition, release.definition_id)
    if definition is None or not definition.is_active:
        raise AgentDeploymentUnavailable("agent_definition_unavailable")
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
            "canary": is_canary,
        },
        "release": {
            "id": release.id,
            "version": release.version,
            "manifest_sha256": release.manifest_sha256,
        },
        "manifest": _json_object(release.manifest_json),
        "validation": validation,
    }
    return ResolvedAgentRelease(deployment, release, snapshot, manifest_digest(snapshot))


def record_run_snapshot(
    db: Session,
    *, request_id: str, session_id: str,
    tenant_key: str, resolved: ResolvedAgentRelease,
) -> AgentRunSnapshot:
    request_id = str(request_id or "").strip()[:160]
    session_id = str(session_id or "").strip()[:160]
    if not request_id or not session_id:
        raise RuntimeError("agent_run_snapshot_identity_required")
    existing = db.query(AgentRunSnapshot).filter(AgentRunSnapshot.request_id == request_id).one_or_none()
    if existing is not None:
        if existing.snapshot_sha256 != resolved.digest:
            raise RuntimeError("agent_run_snapshot_idempotency_conflict")
        return existing
    row = AgentRunSnapshot(
        request_id=request_id,
        session_id=session_id,
        tenant_key=tenant_key[:80],
        deployment_id=resolved.deployment.id,
        release_id=resolved.release.id,
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


def _assert_release_tenant(db: Session, release: AgentRelease, tenant_key: str) -> None:
    definition = db.get(AgentDefinition, release.definition_id)
    if definition is None or definition.tenant_key != tenant_key:
        raise HTTPException(status_code=403, detail="cross_tenant_agent_release_forbidden")


def _deployment_rank(
    row: AgentDeployment,
    *, market_id: int | None, channel: str | None,
    language: str | None, case_type: str | None,
) -> int:
    rank = 0
    for expected, actual, weight in (
        (row.market_id, market_id, 16),
        (_optional_scope(row.channel, 40), _optional_scope(channel, 40), 8),
        (_optional_scope(row.language, 24), _optional_scope(language, 24), 4),
        (_optional_scope(row.case_type, 80), _optional_scope(case_type, 80), 2),
    ):
        if expected is None:
            continue
        if expected != actual:
            return -1
        rank += weight
    return rank


def _cohort_percent(value: str) -> int:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


def _version_reference(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"agent_release_{label}_reference_invalid")
    key = value.get("resource_key") or value.get("profile_key") or value.get("item_key") or value.get("key")
    key = _required_key(key, f"{label}_key")
    version = _positive_int(value.get("version"), f"{label}_version")
    return {"key": key, "version": version}


def _required_key(value: Any, label: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not _SAFE_KEY_RE.fullmatch(cleaned):
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


def _tenant_key(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned or len(cleaned) > 80 or not _SAFE_KEY_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="tenant_key_invalid")
    return cleaned


def _optional_scope(value: Any, max_chars: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().lower().split())
    return cleaned[:max_chars] if cleaned else None


def _json_object(value: Any) -> dict[str, Any]:
    return _bounded_metadata(value) if isinstance(value, dict) else {}


def _bounded_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise HTTPException(status_code=400, detail="agent_release_payload_too_deep")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:8000]
    if isinstance(value, dict):
        if len(value) > 200:
            raise HTTPException(status_code=400, detail="agent_release_payload_too_wide")
        return {str(key)[:160]: _bounded_metadata(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 300:
            raise HTTPException(status_code=400, detail="agent_release_payload_too_large")
        return [_bounded_metadata(item, depth=depth + 1) for item in value]
    raise HTTPException(status_code=400, detail="agent_release_payload_type_invalid")
