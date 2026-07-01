from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AIConfigResource
from ..models_control_plane import KnowledgeItem, PersonaProfile
from .ai_config_service import publish_resource


SUPPORT_CHANNELS = {"whatsapp", "email", "all", "support", "customer"}
STATUS_DICTIONARY_RESOURCE_KEY = "support.status.dictionary"
STATUS_DICTIONARY_SCHEMA_VERSION = 1


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _channel_in_scope(value: str | None) -> bool:
    if not value:
        return True
    return str(value).strip().lower() in SUPPORT_CHANNELS


def _source_item(
    *,
    key: str,
    category: str,
    title: str,
    source_kind: str,
    source_label: str,
    source_path: str | None = None,
    editable: bool,
    effective: bool,
    status: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "category": category,
        "title": title,
        "source_kind": source_kind,
        "source_label": source_label,
        "source_path": source_path,
        "editable": editable,
        "effective": effective,
        "status": status or ("effective" if effective else "inactive"),
        "notes": notes,
    }


def _runtime_legacy_sources() -> list[dict[str, Any]]:
    return [
        _source_item(
            key="runtime.persona.base",
            category="persona",
            title="基础身份与语气",
            source_kind="runtime_file",
            source_label="运行文件",
            source_path="AGENTS / SOUL / IDENTITY / USER",
            editable=False,
            effective=True,
            notes="当前客服助手的人格底座仍来自运行文件；后续应导入到配置库并编译进运行包。",
        ),
        _source_item(
            key="runtime.rules.sop",
            category="rules",
            title="客服处理规则",
            source_kind="runtime_file",
            source_label="运行文件",
            source_path="SUPPORT_SOP",
            editable=False,
            effective=True,
            notes="查件、转人工、失败话术和动作边界当前仍由运行文件约束。",
        ),
        _source_item(
            key="runtime.knowledge.stable",
            category="business_knowledge",
            title="稳定业务知识",
            source_kind="runtime_file",
            source_label="运行文件",
            source_path="SUPPORT_KB",
            editable=False,
            effective=True,
            notes="稳定业务事实当前仍由运行文件提供。",
        ),
        _source_item(
            key="runtime.status.dictionary",
            category="status_dictionary",
            title="状态码客户展示字典",
            source_kind="nexus_config",
            source_label="AIConfigResource",
            source_path=None,
            editable=True,
            effective=True,
            notes="物流状态码到客户可见名称、解释和下一步提示由 Nexus DB 配置；发布后用于后续客户回复。",
        ),
    ]


def _fetch_runtime_cards(bridge_client: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if bridge_client is None:
        return [], {
            "ok": False,
            "status": "retired",
            "message": "Legacy runtime bridge has been retired; use Nexus config resources.",
        }
    client = bridge_client
    try:
        data = client.support_knowledge_config({"operation": "card-list"})
        cards = data.get("cards") if isinstance(data, dict) else None
        if not isinstance(cards, list):
            return [], {"ok": False, "status": "invalid_payload", "message": "运行知识桥返回格式异常"}
        return [card for card in cards if isinstance(card, dict)], {
            "ok": True,
            "status": "connected",
            "count": len(cards),
        }
    except (OSError, RuntimeError) as exc:
        return [], {
            "ok": False,
            "status": "degraded",
            "message": str(exc)[:240],
        }


def _actor_id(actor: Any | None) -> int | None:
    value = getattr(actor, "id", None)
    return value if isinstance(value, int) else None


def _actor_label(actor: Any | None) -> str:
    return str(getattr(actor, "username", None) or getattr(actor, "id", None) or "system")


def _get_status_dictionary_resource(db: Session) -> AIConfigResource | None:
    return (
        db.query(AIConfigResource)
        .filter(
            AIConfigResource.resource_key == STATUS_DICTIONARY_RESOURCE_KEY,
            AIConfigResource.config_type == "status_dictionary",
        )
        .first()
    )


def _ensure_status_dictionary_resource(db: Session, actor: Any | None) -> AIConfigResource:
    row = _get_status_dictionary_resource(db)
    if row is not None:
        return row
    row = AIConfigResource(
        resource_key=STATUS_DICTIONARY_RESOURCE_KEY,
        config_type="status_dictionary",
        name="Support status dictionary",
        description="Customer-visible logistics status labels, explanations, and next actions.",
        scope_type="global",
        scope_value="support",
        is_active=True,
        draft_summary="0 status dictionary entries",
        draft_content_json={
            "schema_version": STATUS_DICTIONARY_SCHEMA_VERSION,
            "entries": [],
            "updated_at": _generated_at(),
            "updated_by": _actor_label(actor),
        },
        created_by=_actor_id(actor),
        updated_by=_actor_id(actor),
    )
    db.add(row)
    db.flush()
    return row


def _string_value(value: Any, *, max_length: int | None = None) -> str:
    text = str(value or "").strip()
    if max_length is not None:
        return text[:max_length]
    return text


def _language_labels(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    labels: dict[str, str] = {}
    for key, label in list(value.items())[:20]:
        clean_key = _string_value(key, max_length=16).lower()
        clean_label = _string_value(label, max_length=240)
        if clean_key and clean_label:
            labels[clean_key] = clean_label
    return labels


def _normalize_status_dictionary_entry(entry: dict[str, Any]) -> dict[str, Any]:
    code = _string_value(entry.get("code"), max_length=32)
    if not code:
        raise ValueError("status_dictionary_entry_code_required")
    return {
        "code": code,
        "label": _string_value(entry.get("label"), max_length=240),
        "desc": _string_value(entry.get("desc"), max_length=1000),
        "action": _string_value(entry.get("action"), max_length=1000),
        "language_labels": _language_labels(entry.get("language_labels")),
        "needs_human": bool(entry.get("needs_human")),
        "promise_eta": bool(entry.get("promise_eta")),
        "editable": bool(entry.get("editable", True)),
    }


def _entries_from_content(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, dict):
        return []
    entries = content.get("entries")
    if not isinstance(entries, list):
        return []
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            normalized.append(_normalize_status_dictionary_entry(entry))
        except ValueError:
            continue
    return normalized


def _entry_map(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry["code"]): entry for entry in entries if entry.get("code")}


def _merge_entries(existing: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = _entry_map(existing)
    for entry in updates:
        merged[entry["code"]] = entry
    return sorted(merged.values(), key=lambda item: item["code"])


def _status_dictionary_content(entries: list[dict[str, Any]], actor: Any | None) -> dict[str, Any]:
    return {
        "schema_version": STATUS_DICTIONARY_SCHEMA_VERSION,
        "entries": entries,
        "updated_at": _generated_at(),
        "updated_by": _actor_label(actor),
    }


def _status_dictionary_summary(entries: list[dict[str, Any]]) -> str:
    count = len(entries)
    return f"{count} status dictionary entr{'y' if count == 1 else 'ies'}"


def _status_dictionary_entry_with_lifecycle(
    *,
    draft: dict[str, Any] | None,
    published: dict[str, Any] | None,
    resource: AIConfigResource | None,
) -> dict[str, Any]:
    base = draft or published or {}
    published_at = _iso(getattr(resource, "published_at", None))
    updated_at = _iso(getattr(resource, "updated_at", None))
    lifecycle = "default"
    if draft and published:
        lifecycle = "published" if draft == published else "changed"
    elif draft:
        lifecycle = "draft"
    elif published:
        lifecycle = "published"
    return _status_dictionary_entry_out({
        **base,
        "default_label": published.get("label", "") if published else "",
        "default_desc": published.get("desc", "") if published else "",
        "default_action": published.get("action", "") if published else "",
        "published_label": published.get("label", "") if published else "",
        "published_desc": published.get("desc", "") if published else "",
        "published_action": published.get("action", "") if published else "",
        "draft_label": draft.get("label", "") if draft else "",
        "draft_desc": draft.get("desc", "") if draft else "",
        "draft_action": draft.get("action", "") if draft else "",
        "status": lifecycle,
        "editable": base.get("editable", True),
        "published_at": published_at,
        "updated_at": updated_at,
    })


def get_status_dictionary_bundle(db: Session) -> dict[str, Any]:
    row = _get_status_dictionary_resource(db)
    draft_entries = _entries_from_content(row.draft_content_json if row else None)
    published_entries = _entries_from_content(row.published_content_json if row else None)
    draft_by_code = _entry_map(draft_entries)
    published_by_code = _entry_map(published_entries)
    all_codes = sorted(set(draft_by_code) | set(published_by_code))
    entries = [
        _status_dictionary_entry_with_lifecycle(
            draft=draft_by_code.get(code),
            published=published_by_code.get(code),
            resource=row,
        )
        for code in all_codes
    ]
    status_value = "empty"
    if published_entries:
        status_value = "ready"
    elif draft_entries:
        status_value = "draft"
    return {
        "ok": True,
        "status": status_value,
        "source": "nexus_ai_config_resources",
        "resource_key": STATUS_DICTIONARY_RESOURCE_KEY,
        "resource_id": row.id if row else None,
        "entries": entries,
        "count": len(entries),
        "draft_count": len(draft_entries),
        "published_count": len(published_entries),
        "published_version": row.published_version if row else 0,
        "published_at": _iso(row.published_at) if row else None,
        "updated_at": _iso(row.updated_at) if row else None,
        "message": "Status dictionary is stored in Nexus DB; legacy runtime bridge is not used.",
    }


def save_status_dictionary_draft(db: Session, entries: list[dict[str, Any]], actor: Any | None) -> dict[str, Any]:
    normalized = [_normalize_status_dictionary_entry(entry) for entry in entries]
    if not normalized:
        raise ValueError("status_dictionary_entry_required")
    row = _ensure_status_dictionary_resource(db, actor)
    current_entries = _entries_from_content(row.draft_content_json)
    merged = _merge_entries(current_entries, normalized)
    row.draft_content_json = _status_dictionary_content(merged, actor)
    row.draft_summary = _status_dictionary_summary(merged)
    row.updated_by = _actor_id(actor)
    db.flush()
    return get_status_dictionary_bundle(db)


def publish_status_dictionary(db: Session, entries: list[dict[str, Any]] | None, actor: Any | None) -> dict[str, Any]:
    if entries:
        save_status_dictionary_draft(db, entries, actor)
    row = _ensure_status_dictionary_resource(db, actor)
    draft_entries = _entries_from_content(row.draft_content_json)
    if not draft_entries:
        raise ValueError("status_dictionary_entry_required")
    row.draft_content_json = _status_dictionary_content(draft_entries, actor)
    row.draft_summary = _status_dictionary_summary(draft_entries)
    publish_resource(db, row, actor, notes="Publish support status dictionary")
    db.flush()
    return get_status_dictionary_bundle(db)


def _status_dictionary_entry_out(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(entry.get("code") or ""),
        "label": entry.get("label") or "",
        "desc": entry.get("desc") or "",
        "action": entry.get("action") or "",
        "language_labels": entry.get("language_labels") if isinstance(entry.get("language_labels"), dict) else {},
        "needs_human": bool(entry.get("needs_human")),
        "promise_eta": bool(entry.get("promise_eta")),
        "default_label": entry.get("default_label") or "",
        "default_desc": entry.get("default_desc") or "",
        "default_action": entry.get("default_action") or "",
        "published_label": entry.get("published_label") or "",
        "published_desc": entry.get("published_desc") or "",
        "published_action": entry.get("published_action") or "",
        "draft_label": entry.get("draft_label") or "",
        "draft_desc": entry.get("draft_desc") or "",
        "draft_action": entry.get("draft_action") or "",
        "status": entry.get("status") or "default",
        "editable": bool(entry.get("editable", True)),
        "published_at": entry.get("published_at"),
        "updated_at": entry.get("updated_at"),
    }


def _runtime_card_out(card: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(card.get("customer_visible")) and bool(card.get("ai_enabled"))
    status = str(card.get("status") or "draft")
    return {
        "id": card.get("id") or card.get("workspace_path") or card.get("title"),
        "title": card.get("title") or "未命名知识卡片",
        "category": "customer_knowledge",
        "country": card.get("country") or "Global",
        "channel": "whatsapp",
        "language": card.get("language") or "auto",
        "status": status,
        "enabled": enabled and status == "published",
        "customer_visible": bool(card.get("customer_visible")),
        "ai_enabled": bool(card.get("ai_enabled")),
        "runtime_scope": card.get("runtime_scope"),
        "owner": card.get("owner") or "",
        "review_due_at": card.get("review_due_at") or None,
        "expires_at": card.get("expires_at") or None,
        "published_at": card.get("published_at") or None,
        "updated_at": card.get("updated_at") or None,
        "source_kind": "runtime_file",
        "source_label": "运行知识卡片",
        "source_path": card.get("workspace_path") or None,
        "editable": True,
        "summary": (card.get("customer_answer") or "")[:220],
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
        "summary": row.summary or row.fact_answer or (row.published_body or row.draft_body or "")[:220],
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


def build_support_intelligence_config(
    db: Session,
    *,
    bridge_client: Any | None = None,
) -> dict[str, Any]:
    runtime_cards_raw, bridge_status = _fetch_runtime_cards(bridge_client)
    runtime_cards = [_runtime_card_out(card) for card in runtime_cards_raw]
    status_dictionary_bundle = get_status_dictionary_bundle(db)
    status_dictionary_entries = status_dictionary_bundle["entries"]
    status_dictionary_status = {
        key: value
        for key, value in status_dictionary_bundle.items()
        if key != "entries"
    }

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
        row for row in ai_config_rows
        if row.config_type in {"persona", "knowledge", "rule", "rules", "status_dictionary", "channel_policy", "support_runtime"}
    ]
    ai_type_counts = Counter(row.config_type for row in support_ai_configs)
    db_channel_counts = Counter((row.channel or "all") for row in support_knowledge)

    areas = [
        {
            "key": "persona",
            "title": "人格与语气",
            "description": "客服身份、说话风格、禁用表达和不确定时的话术。",
            "configurable_count": len(support_personas),
            "runtime_effective_count": 1,
            "state": "hybrid",
        },
        {
            "key": "customer_knowledge",
            "title": "客服知识库",
            "description": "政策、FAQ、国家说明、异常处理口径和客户可见知识卡片。",
            "configurable_count": len(support_knowledge) + len(runtime_cards),
            "runtime_effective_count": sum(1 for card in runtime_cards if card["enabled"]),
            "state": "runtime_cards_connected" if bridge_status.get("ok") else "degraded",
        },
        {
            "key": "rules",
            "title": "SOP / 行为规则",
            "description": "何时查接口、何时转人工、哪些动作需要客户确认。",
            "configurable_count": ai_type_counts.get("rule", 0) + ai_type_counts.get("rules", 0),
            "runtime_effective_count": 1,
            "state": "legacy_runtime_file",
        },
        {
            "key": "status_dictionary",
            "title": "状态码 / 术语字典",
            "description": "接口状态码、客户可见名称、多语言解释和下一步提示。",
            "configurable_count": len(status_dictionary_entries) or ai_type_counts.get("status_dictionary", 0),
            "runtime_effective_count": int(status_dictionary_status.get("published_count") or 0),
            "state": status_dictionary_status.get("status") or "empty",
        },
        {
            "key": "channel_policy",
            "title": "渠道策略",
            "description": "WhatsApp、Email 的自动回复、草稿、长度、签名和接管策略。",
            "configurable_count": ai_type_counts.get("channel_policy", 0),
            "runtime_effective_count": 0,
            "state": "control_plane_ready",
        },
    ]

    generated_at = _generated_at()
    bundle = {
        "key": "support.runtime.config",
        "version_label": f"preview-{generated_at.replace(':', '').replace('-', '')[:15]}",
        "mode": "hybrid_runtime_import",
        "channels": ["whatsapp", "email"],
        "source_of_truth_target": "speedy_console_config_center",
        "current_runtime": "legacy_files_plus_runtime_cards",
        "ready_for_full_cutover": False,
    }

    gaps = []
    if not bridge_status.get("ok"):
        gaps.append("当前运行知识桥不可用，页面只能显示数据库配置，不能确认客服助手真实知识卡片。")
    if not support_personas:
        gaps.append("配置库还没有 support 渠道的人格配置；当前仍由运行文件提供人格。")
    if not status_dictionary_entries:
        gaps.append("状态词典还没有 Nexus DB 配置；请先保存草稿并发布。")
    if not ai_type_counts.get("rule") and not ai_type_counts.get("rules"):
        gaps.append("SOP 行为规则还没有结构化配置；当前仍由运行文件提供。")

    return {
        "generated_at": generated_at,
        "bundle": bundle,
        "bridge_status": bridge_status,
        "areas": areas,
        "runtime_sources": _runtime_legacy_sources(),
        "runtime_knowledge_cards": runtime_cards,
        "status_dictionary_entries": status_dictionary_entries,
        "status_dictionary_status": status_dictionary_status,
        "config_library": {
            "personas": [_persona_out(row) for row in support_personas],
            "knowledge_items": [_knowledge_item_out(row) for row in support_knowledge],
            "ai_configs": [_ai_config_out(row) for row in support_ai_configs],
            "counts": {
                "personas": len(support_personas),
                "knowledge_items": len(support_knowledge),
                "runtime_knowledge_cards": len(runtime_cards),
                "ai_configs": len(support_ai_configs),
                "ai_config_types": dict(ai_type_counts),
                "knowledge_channels": dict(db_channel_counts),
            },
        },
        "migration_target": {
            "principle": "前端自然语言配置，后端结构化编译生效。",
            "target_source_of_truth": "Speedy Console 配置中心",
            "runtime_package": "发布后生成不可变的 support runtime config bundle，客服助手每轮只读当前发布版。",
            "legacy_imports": [
                "运行人格文件",
                "客服规则文件",
                "稳定业务知识文件",
                "客户知识卡片",
                "状态码代码字典",
            ],
        },
        "gaps": gaps,
    }
