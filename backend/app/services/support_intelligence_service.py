from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AIConfigResource
from ..models_control_plane import KnowledgeItem, PersonaProfile

SUPPORT_CHANNELS = {"whatsapp", "email", "all", "support", "customer"}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _channel_in_scope(value: str | None) -> bool:
    if not value:
        return True
    return str(value).strip().lower() in SUPPORT_CHANNELS


def _source_item(
    *,
    key: str,
    category: str,
    title: str,
    source_label: str,
    configurable_count: int,
    effective_count: int,
) -> dict[str, Any]:
    return {
        "key": key,
        "category": category,
        "title": title,
        "source_kind": "canonical_control_plane",
        "source_label": source_label,
        "editable": True,
        "effective": effective_count > 0,
        "status": "effective" if effective_count > 0 else "unconfigured",
        "configurable_count": configurable_count,
        "effective_count": effective_count,
    }


def _persona_out(row: PersonaProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.profile_key,
        "title": row.name,
        "channel": row.channel,
        "language": row.language,
        "status": "published" if row.published_version > 0 else "draft",
        "active": bool(row.is_active),
        "published_version": row.published_version,
        "published_at": _iso(row.published_at),
        "updated_at": _iso(row.updated_at),
        "summary": row.published_summary or row.draft_summary or row.description,
    }


def _knowledge_item_out(row: KnowledgeItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.item_key,
        "title": row.title,
        "kind": row.knowledge_kind,
        "channel": row.channel,
        "audience_scope": row.audience_scope,
        "language": row.language,
        "status": row.status,
        "priority": row.priority,
        "published_version": row.published_version,
        "published_at": _iso(row.published_at),
        "indexed_at": _iso(row.indexed_at),
        "summary": row.summary
        or row.fact_answer
        or (row.published_body or row.draft_body or "")[:220],
    }


def _ai_config_out(row: AIConfigResource) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.resource_key,
        "title": row.name,
        "config_type": row.config_type,
        "scope_type": row.scope_type,
        "scope_value": row.scope_value,
        "status": "published" if row.published_version > 0 else "draft",
        "active": bool(row.is_active),
        "published_version": row.published_version,
        "published_at": _iso(row.published_at),
        "updated_at": _iso(row.updated_at),
        "summary": row.published_summary or row.draft_summary or row.description,
    }


def build_support_intelligence_config(db: Session) -> dict[str, Any]:
    persona_rows = (
        db.query(PersonaProfile)
        .order_by(PersonaProfile.updated_at.desc(), PersonaProfile.id.desc())
        .limit(200)
        .all()
    )
    knowledge_rows = (
        db.query(KnowledgeItem)
        .order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc())
        .limit(300)
        .all()
    )
    ai_config_rows = (
        db.query(AIConfigResource)
        .order_by(AIConfigResource.updated_at.desc(), AIConfigResource.id.desc())
        .limit(300)
        .all()
    )

    support_personas = [row for row in persona_rows if _channel_in_scope(row.channel)]
    support_knowledge = [row for row in knowledge_rows if _channel_in_scope(row.channel)]
    support_ai_configs = [
        row
        for row in ai_config_rows
        if row.config_type
        in {
            "persona",
            "knowledge",
            "rule",
            "rules",
            "status_dictionary",
            "channel_policy",
            "support_runtime",
        }
    ]
    ai_type_counts = Counter(row.config_type for row in support_ai_configs)
    db_channel_counts = Counter((row.channel or "all") for row in support_knowledge)
    published_personas = sum(
        1 for row in support_personas if row.is_active and row.published_version > 0
    )
    published_knowledge = sum(
        1 for row in support_knowledge if row.status == "published" and row.published_version > 0
    )
    published_rules = sum(
        1
        for row in support_ai_configs
        if row.config_type in {"rule", "rules"}
        and row.is_active
        and row.published_version > 0
    )
    published_status_dictionary = sum(
        1
        for row in support_ai_configs
        if row.config_type == "status_dictionary"
        and row.is_active
        and row.published_version > 0
    )
    published_channel_policy = sum(
        1
        for row in support_ai_configs
        if row.config_type == "channel_policy"
        and row.is_active
        and row.published_version > 0
    )

    areas = [
        {
            "key": "persona",
            "title": "人格与语气",
            "description": "客服身份、说话风格、禁用表达和不确定时的话术。",
            "configurable_count": len(support_personas),
            "runtime_effective_count": published_personas,
            "state": "ready" if published_personas else "unconfigured",
        },
        {
            "key": "customer_knowledge",
            "title": "客服知识库",
            "description": "政策、FAQ、国家说明、异常处理口径和客户可见知识。",
            "configurable_count": len(support_knowledge),
            "runtime_effective_count": published_knowledge,
            "state": "ready" if published_knowledge else "unconfigured",
        },
        {
            "key": "rules",
            "title": "SOP / 行为规则",
            "description": "何时查接口、何时转人工、哪些动作需要客户确认。",
            "configurable_count": ai_type_counts.get("rule", 0)
            + ai_type_counts.get("rules", 0),
            "runtime_effective_count": published_rules,
            "state": "ready" if published_rules else "unconfigured",
        },
        {
            "key": "status_dictionary",
            "title": "状态码 / 术语字典",
            "description": "接口状态码、客户可见名称、多语言解释和下一步提示。",
            "configurable_count": ai_type_counts.get("status_dictionary", 0),
            "runtime_effective_count": published_status_dictionary,
            "state": "ready" if published_status_dictionary else "unconfigured",
        },
        {
            "key": "channel_policy",
            "title": "渠道策略",
            "description": "WhatsApp、Email 的自动回复、草稿、长度、签名和接管策略。",
            "configurable_count": ai_type_counts.get("channel_policy", 0),
            "runtime_effective_count": published_channel_policy,
            "state": "ready" if published_channel_policy else "unconfigured",
        },
    ]

    generated_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    ready_for_runtime = bool(
        published_personas and published_knowledge and published_rules
    )
    gaps: list[str] = []
    if not published_personas:
        gaps.append("配置库还没有已发布的 support 渠道人格配置。")
    if not published_knowledge:
        gaps.append("配置库还没有已发布的客户可见知识。")
    if not published_rules:
        gaps.append("配置库还没有已发布的 SOP 行为规则。")
    if not published_status_dictionary:
        gaps.append("配置库还没有已发布的状态词典资源。")

    runtime_sources = [
        _source_item(
            key="control.persona",
            category="persona",
            title="人格配置",
            source_label="PersonaProfile",
            configurable_count=len(support_personas),
            effective_count=published_personas,
        ),
        _source_item(
            key="control.knowledge",
            category="business_knowledge",
            title="客服知识",
            source_label="KnowledgeItem",
            configurable_count=len(support_knowledge),
            effective_count=published_knowledge,
        ),
        _source_item(
            key="control.rules",
            category="rules",
            title="行为规则与状态词典",
            source_label="AIConfigResource",
            configurable_count=len(support_ai_configs),
            effective_count=published_rules + published_status_dictionary,
        ),
    ]

    return {
        "generated_at": generated_at,
        "bundle": {
            "key": "support.runtime.config",
            "version_label": f"preview-{generated_at.replace(':', '').replace('-', '')[:15]}",
            "mode": "canonical_control_plane",
            "channels": ["whatsapp", "email"],
            "source_of_truth": "nexus_config_library",
            "ready_for_runtime": ready_for_runtime,
        },
        "runtime_status": {
            "ok": ready_for_runtime,
            "status": "ready" if ready_for_runtime else "configuration_required",
            "authority": "nexus_control_plane",
            "external_runtime_bridge": False,
        },
        "areas": areas,
        "runtime_sources": runtime_sources,
        "config_library": {
            "personas": [_persona_out(row) for row in support_personas],
            "knowledge_items": [_knowledge_item_out(row) for row in support_knowledge],
            "ai_configs": [_ai_config_out(row) for row in support_ai_configs],
            "counts": {
                "personas": len(support_personas),
                "knowledge_items": len(support_knowledge),
                "ai_configs": len(support_ai_configs),
                "ai_config_types": dict(ai_type_counts),
                "knowledge_channels": dict(db_channel_counts),
            },
        },
        "gaps": gaps,
    }
