from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models_control_plane import PersonaProfile, PersonaProfileReview, PersonaProfileVersion
from ..utils.time import utc_now
from . import persona_service
from .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities

PERSONA_BUILDER_CAPABILITIES = {CAP_AI_CONFIG_READ, CAP_AI_CONFIG_MANAGE}


def _value(raw: Any) -> Any:
    return raw.value if hasattr(raw, "value") else raw


def _tone(value: int, *, danger: int, warning: int = 1) -> str:
    if value >= danger:
        return "danger"
    if value >= warning:
        return "warning"
    return "success"


def _kpi(key: str, label: str, value: int, hint: str, tone: str = "default") -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "hint": hint, "tone": tone}


def _template_block(key: str, label: str, backend_contract: str, status_value: str, evidence: str, href: str) -> dict[str, str]:
    return {"key": key, "label": label, "backend_contract": backend_contract, "status": status_value, "evidence": evidence, "href": href}


def _lifecycle_step(key: str, step: str, owner: str, artifact: str, status_value: str, count: int, href: str, enabled: bool) -> dict[str, Any]:
    return {
        "key": key,
        "step": step,
        "owner": owner,
        "artifact": artifact,
        "status": status_value,
        "count": count,
        "href": href,
        "enabled": enabled,
    }


def _has_draft_content(row: PersonaProfile) -> bool:
    return bool((row.draft_summary or "").strip()) or bool(row.draft_content_json or {})


def _content(row: PersonaProfile) -> dict[str, Any]:
    if isinstance(row.published_content_json, dict) and row.published_content_json:
        return row.published_content_json
    if isinstance(row.draft_content_json, dict):
        return row.draft_content_json
    return {}


def _identity_ready(content: dict[str, Any]) -> bool:
    return bool(content.get("brand_name") or content.get("assistant_name") or content.get("identity_statement"))


def _boundary_ready(content: dict[str, Any]) -> bool:
    guardrails = content.get("guardrails")
    disallowed = content.get("disallowed_identity_claims")
    return bool(content.get("handoff_boundary") or guardrails or disallowed)


def _guardrail_count(content: dict[str, Any]) -> int:
    total = 0
    for key in ("guardrails", "disallowed_identity_claims", "capabilities"):
        value = content.get(key)
        if isinstance(value, list):
            total += len([item for item in value if str(item or "").strip()])
        elif isinstance(value, str) and value.strip():
            total += 1
    return total


def _scope_label(row: PersonaProfile) -> str:
    return " / ".join([
        f"market:{row.market_id}" if row.market_id is not None else "market:global",
        f"channel:{row.channel or 'global'}",
        f"lang:{row.language or 'global'}",
    ])


def _scope_specificity(row: PersonaProfile) -> int:
    return int(row.market_id is not None) + int(bool(row.channel)) + int(bool(row.language))


def _draft_changed(row: PersonaProfile) -> bool:
    return (row.draft_summary or None) != (row.published_summary or None) or (row.draft_content_json or {}) != (row.published_content_json or {})


def _profile(row: PersonaProfile) -> dict[str, Any]:
    content = _content(row)
    draft_ready = _has_draft_content(row)
    published_ready = bool(row.is_active and row.published_version > 0)
    risk_flags: list[str] = []
    if not _identity_ready(content):
        risk_flags.append("identity_context_missing")
    if not _boundary_ready(content):
        risk_flags.append("handoff_boundary_or_guardrails_missing")
    if not row.channel:
        risk_flags.append("global_channel_fallback")
    if not row.language:
        risk_flags.append("global_language_fallback")
    if not row.is_active:
        risk_flags.append("inactive")
    return {
        "id": row.id,
        "profile_key": row.profile_key,
        "name": row.name,
        "description": row.description,
        "market_id": row.market_id,
        "channel": row.channel,
        "language": row.language,
        "scope_label": _scope_label(row),
        "scope_specificity": _scope_specificity(row),
        "is_active": row.is_active,
        "published_version": row.published_version,
        "draft_ready": draft_ready,
        "published_ready": published_ready,
        "needs_publish": draft_ready and (row.published_version == 0 or _draft_changed(row)),
        "identity_ready": _identity_ready(content),
        "boundary_ready": _boundary_ready(content),
        "guardrail_count": _guardrail_count(content),
        "risk_flags": risk_flags,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "href": "/ai-control",
        "evidence": "published persona selected by runtime" if published_ready else "draft saved in persona_profiles",
    }


def _review(row: PersonaProfileReview, profiles: dict[int, PersonaProfile]) -> dict[str, Any]:
    profile = profiles.get(row.profile_id)
    snapshot = row.snapshot_json or {}
    return {
        "id": row.id,
        "profile_id": row.profile_id,
        "profile_key": profile.profile_key if profile else snapshot.get("profile_key"),
        "profile_name": profile.name if profile else snapshot.get("name"),
        "review_version": row.review_version,
        "status": row.status,
        "summary": row.summary,
        "notes": row.notes,
        "scope_label": " / ".join([
            f"market:{snapshot.get('market_id')}" if snapshot.get("market_id") is not None else "market:global",
            f"channel:{snapshot.get('channel') or 'global'}",
            f"lang:{snapshot.get('language') or 'global'}",
        ]),
        "requested_by": row.requested_by,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "decision_note": row.decision_note,
        "release_window_start": row.release_window_start.isoformat() if row.release_window_start else None,
        "release_window_end": row.release_window_end.isoformat() if row.release_window_end else None,
        "published_by": row.published_by,
        "published_version": row.published_version,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "href": "/persona-builder",
        "evidence": "persona_profile_reviews approval workflow",
    }


def _resolve(db: Session, *, market_id: int | None, channel: str | None, language: str | None) -> dict[str, Any]:
    profile, rank = persona_service.resolve_preview(db, market_id=market_id, channel=channel, language=language)
    reasons: list[str] = []
    if profile:
        if profile.market_id == market_id and market_id is not None:
            reasons.append("market_exact")
        elif profile.market_id is None:
            reasons.append("market_global")
        if profile.channel == channel and channel:
            reasons.append("channel_exact")
        elif profile.channel is None:
            reasons.append("channel_global")
        if profile.language == language and language:
            reasons.append("language_exact")
        elif profile.language is None:
            reasons.append("language_global")
    return {
        "market_id": market_id,
        "channel": channel,
        "language": language,
        "matched_profile_key": profile.profile_key if profile else None,
        "matched_name": profile.name if profile else None,
        "match_rank": rank,
        "published_version": profile.published_version if profile else None,
        "reasons": reasons,
        "fallback": profile is not None and (profile.market_id is None or profile.channel is None or profile.language is None),
        "status": "matched" if profile else "no_match",
        "href": "/persona-builder",
    }


def _simulation_scenarios(rows: list[PersonaProfile], db: Session) -> list[dict[str, Any]]:
    contexts: list[tuple[int | None, str | None, str | None]] = []
    for row in sorted(rows, key=lambda item: (-_scope_specificity(item), item.profile_key)):
        if row.is_active and row.published_version > 0:
            context = (row.market_id, row.channel, row.language)
            if context not in contexts:
                contexts.append(context)
        if len(contexts) >= 4:
            break
    for context in [(None, "webchat", "en"), (None, "email", "en"), (None, "website", None)]:
        if context not in contexts:
            contexts.append(context)
        if len(contexts) >= 6:
            break
    return [_resolve(db, market_id=market_id, channel=channel, language=language) for market_id, channel, language in contexts[:6]]


def build_persona_builder(db: Session, current_user) -> dict[str, Any]:
    now = utc_now()
    capabilities = resolve_capabilities(current_user, db)
    if not (capabilities & PERSONA_BUILDER_CAPABILITIES):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="persona_builder_requires_ai_config_capability")

    rows = (
        db.query(PersonaProfile)
        .order_by(PersonaProfile.is_active.desc(), PersonaProfile.published_version.desc(), PersonaProfile.profile_key.asc())
        .limit(200)
        .all()
    )
    profile_map = {row.id: row for row in rows}
    reviews = (
        db.query(PersonaProfileReview)
        .order_by(PersonaProfileReview.requested_at.desc(), PersonaProfileReview.id.desc())
        .limit(100)
        .all()
    )
    total = len(rows)
    active_count = sum(1 for row in rows if row.is_active)
    published_count = sum(1 for row in rows if row.published_version > 0)
    draft_ready_count = sum(1 for row in rows if _has_draft_content(row))
    needs_publish_count = sum(1 for row in rows if _has_draft_content(row) and (row.published_version == 0 or _draft_changed(row)))
    global_fallback_count = sum(1 for row in rows if row.is_active and row.published_version > 0 and (row.channel is None or row.language is None or row.market_id is None))
    identity_ready_count = sum(1 for row in rows if _identity_ready(_content(row)))
    boundary_ready_count = sum(1 for row in rows if _boundary_ready(_content(row)))
    version_count = int(db.query(func.count(PersonaProfileVersion.id)).scalar() or 0)
    pending_review_count = sum(1 for row in reviews if row.status == "pending")
    approved_review_count = sum(1 for row in reviews if row.status == "approved")
    published_review_count = sum(1 for row in reviews if row.status == "published")
    manage_enabled = CAP_AI_CONFIG_MANAGE in capabilities

    return {
        "generated_at": now.isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "kpis": [
            _kpi("total_profiles", "Persona", total, "persona_profiles 中的真实人格配置", _tone(total, danger=60, warning=1)),
            _kpi("published_profiles", "已发布", published_count, "published_version > 0 且可被 resolve-preview 选择", _tone(published_count, danger=60, warning=1)),
            _kpi("draft_ready", "草稿就绪", draft_ready_count, "存在 draft_summary 或 draft_content_json", _tone(draft_ready_count, danger=60, warning=1)),
            _kpi("needs_publish", "待发布变更", needs_publish_count, "草稿与已发布版本不一致或尚未发布", _tone(needs_publish_count, danger=3, warning=1)),
            _kpi("fallback_routes", "Fallback 路由", global_fallback_count, "使用 global market/channel/language 的已发布 fallback", _tone(global_fallback_count, danger=8, warning=1)),
            _kpi("version_count", "版本记录", version_count, "persona_profile_versions 中的发布/回滚证据", _tone(version_count, danger=80, warning=1)),
            _kpi("pending_reviews", "待审批", pending_review_count, "persona_profile_reviews status=pending", _tone(pending_review_count, danger=5, warning=1)),
        ],
        "profiles": [_profile(row) for row in rows[:50]],
        "approval_queue": [_review(row, profile_map) for row in reviews[:50]],
        "simulation_scenarios": _simulation_scenarios(rows, db),
        "release_lifecycle": [
            _lifecycle_step("draft", "Draft", "AI Ops / Product", "PersonaProfile draft_summary + draft_content_json", "implemented", draft_ready_count, "/ai-control", manage_enabled),
            _lifecycle_step("simulation", "Simulation", "AI Ops / Product", "POST /api/persona-profiles/resolve-preview", "implemented", published_count, "/persona-builder", True),
            _lifecycle_step("impact-preview", "Impact Preview", "Manager / AI Ops", "scope and fallback route read-model", "linked", total, "/persona-builder", True),
            _lifecycle_step("approval", "Approval", "Manager / Admin", "submit-review / approve / reject command", "implemented", pending_review_count + approved_review_count, "/persona-builder", manage_enabled),
            _lifecycle_step("release-window", "Release Window", "Manager / AI Ops", "approved review publish gate", "implemented", approved_review_count, "/persona-builder", manage_enabled),
            _lifecycle_step("published", "Published", "AI Ops / Product", "PersonaProfileVersion + published_content_json", "implemented" if published_count else "linked", published_count, "/ai-control", manage_enabled),
            _lifecycle_step("rollback", "Rollback", "AI Ops / Product", "POST /api/persona-profiles/{id}/rollback", "implemented" if version_count else "linked", version_count, "/ai-control", manage_enabled),
            _lifecycle_step("runtime-evidence", "Runtime Evidence", "AI Ops / Auditor", "webchat runtime context persona_context", "linked", identity_ready_count + boundary_ready_count, "/persona-builder", True),
        ],
        "template_blocks": [
            _template_block("persona-list", "Persona List", "GET /api/persona-profiles", "implemented", "读取真实 PersonaProfile 列表、scope、启用状态和版本字段", "/persona-builder"),
            _template_block("persona-editor", "Editor / Draft Save", "POST/PATCH /api/persona-profiles", "implemented", "草稿摘要、身份字段、边界和 guardrails 保存到后端表", "/ai-control"),
            _template_block("resolve-preview", "Simulation / Resolve Preview", "POST /api/persona-profiles/resolve-preview", "implemented", "按 market/channel/language 返回真实匹配 profile 和 match_rank", "/persona-builder"),
            _template_block("publish-rollback", "Publish / Rollback", "POST /api/persona-profiles/{id}/publish|rollback", "implemented", "发布创建 PersonaProfileVersion；回滚复制旧快照为新版本", "/ai-control"),
            _template_block("approval", "Approval / Release Window", "POST /api/persona-profiles/{id}/submit-review + /reviews/{id}/approve|reject|publish", "implemented", "审批流写入 persona_profile_reviews，并可按 release window 发布已审批快照", "/persona-builder"),
            _template_block("runtime-evidence", "Runtime Evidence", "build_webchat_runtime_context persona_context", "linked", "运行时读取已发布 Persona；专用 runtime evidence 查询端点仍未实现", "/persona-builder"),
        ],
        "facts": {
            "active_profiles": active_count,
            "published_profiles": published_count,
            "draft_ready_profiles": draft_ready_count,
            "needs_publish_profiles": needs_publish_count,
            "global_fallback_profiles": global_fallback_count,
            "identity_ready_profiles": identity_ready_count,
            "boundary_ready_profiles": boundary_ready_count,
            "version_count": version_count,
            "pending_review_count": pending_review_count,
            "approved_review_count": approved_review_count,
            "published_review_count": published_review_count,
            "ai_config_read_capability": CAP_AI_CONFIG_READ in capabilities,
            "ai_config_manage_capability": CAP_AI_CONFIG_MANAGE in capabilities,
            "submit_review_endpoint": "implemented",
            "approval_endpoint": "implemented",
            "release_window_command": "implemented",
            "dedicated_runtime_evidence_endpoint": "not_implemented",
        },
    }
