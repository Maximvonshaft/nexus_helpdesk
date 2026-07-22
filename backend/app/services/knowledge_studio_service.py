from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeChunk, KnowledgeItem, KnowledgeItemVersion
from ..utils.time import utc_now
from .agent_release_service import authoritative_tenant_key
from .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities

KNOWLEDGE_STUDIO_CAPABILITIES = {CAP_AI_CONFIG_READ, CAP_AI_CONFIG_MANAGE}


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


def _has_draft_content(row: KnowledgeItem) -> bool:
    if (row.draft_body or "").strip() or (row.draft_normalized_text or "").strip():
        return True
    if row.knowledge_kind in {"faq", "business_fact"}:
        return bool((row.fact_question or "").strip() and (row.fact_answer or "").strip())
    return False


def _scope_label(row: KnowledgeItem) -> str:
    parts = [
        f"market:{row.market_id}" if row.market_id is not None else "market:global",
        f"channel:{row.channel or 'global'}",
        f"audience:{row.audience_scope or 'customer'}",
        f"lang:{row.language or 'global'}",
    ]
    return " / ".join(parts)


def _conflict_terms(row: KnowledgeItem) -> list[str]:
    terms: list[str] = []
    if row.fact_question:
        terms.append(row.fact_question)
    terms.extend(str(item) for item in (row.fact_aliases_json or []) if str(item).strip())
    if not terms and row.title:
        terms.append(row.title)
    normalized: list[str] = []
    for term in terms:
        value = " ".join(str(term).strip().lower().split())
        if len(value) >= 4 and value not in normalized:
            normalized.append(value)
    return normalized[:8]


def _conflict_scope(row: KnowledgeItem) -> tuple[Any, ...]:
    return (row.market_id, row.channel or "*", row.audience_scope or "customer", row.language or "*")


def _build_conflicts(rows: list[KnowledgeItem]) -> tuple[list[dict[str, Any]], set[int]]:
    buckets: dict[tuple[Any, ...], list[KnowledgeItem]] = defaultdict(list)
    for row in rows:
        if row.status == "archived":
            continue
        for term in _conflict_terms(row):
            buckets[(*_conflict_scope(row), term)].append(row)

    conflicts: list[dict[str, Any]] = []
    conflicting_ids: set[int] = set()
    for key, items in buckets.items():
        unique = {item.id: item for item in items}
        if len(unique) < 2:
            continue
        item_rows = sorted(unique.values(), key=lambda item: (item.priority, item.item_key))
        conflicting_ids.update(item.id for item in item_rows)
        conflicts.append(
            {
                "key": f"conflict:{key[-1]}:{len(conflicts) + 1}",
                "term": key[-1],
                "scope": _scope_label(item_rows[0]),
                "item_ids": [item.id for item in item_rows],
                "item_keys": [item.item_key for item in item_rows],
                "titles": [item.title for item in item_rows],
                "status": "needs_review",
                "blocker": any(item.published_version > 0 or item.status == "active" for item in item_rows),
                "href": "/ai-control",
                "evidence": [
                    f"{item.item_key}: status={item.status}, published_version={item.published_version}, priority={item.priority}"
                    for item in item_rows
                ],
            }
        )
    conflicts.sort(key=lambda item: (not item["blocker"], item["term"]))
    return conflicts, conflicting_ids


def run_conflict_check(
    db: Session, payload, *, tenant_id: str | None = None
) -> dict[str, Any]:
    query = db.query(KnowledgeItem)
    if tenant_id is not None:
        query = query.filter(KnowledgeItem.tenant_id == tenant_id)
    if not getattr(payload, "include_archived", False):
        query = query.filter(KnowledgeItem.status != "archived")
    if getattr(payload, "market_id", None) is not None:
        query = query.filter(KnowledgeItem.market_id == payload.market_id)
    if getattr(payload, "channel", None):
        query = query.filter(KnowledgeItem.channel == payload.channel)
    if getattr(payload, "audience_scope", None):
        query = query.filter(KnowledgeItem.audience_scope == payload.audience_scope)
    if getattr(payload, "language", None):
        query = query.filter(KnowledgeItem.language == payload.language)

    rows = (
        query.order_by(KnowledgeItem.status.asc(), KnowledgeItem.priority.asc(), KnowledgeItem.updated_at.desc(), KnowledgeItem.item_key.asc())
        .limit(500)
        .all()
    )
    conflicts, _conflicting_ids = _build_conflicts(rows)
    item_id = getattr(payload, "item_id", None)
    if item_id is not None:
        conflicts = [item for item in conflicts if item_id in set(item.get("item_ids") or [])]

    needle = " ".join(str(getattr(payload, "q", "") or "").strip().lower().split())
    if needle:
        conflicts = [
            item
            for item in conflicts
            if needle in _conflict_search_text(item)
        ]

    limit = max(1, min(int(getattr(payload, "limit", 12) or 12), 50))
    return {
        "generated_at": utc_now(),
        "total": len(conflicts),
        "conflicts": conflicts[:limit],
        "filters": {
            "q": getattr(payload, "q", None),
            "item_id": item_id,
            "market_id": getattr(payload, "market_id", None),
            "channel": getattr(payload, "channel", None),
            "audience_scope": getattr(payload, "audience_scope", None),
            "language": getattr(payload, "language", None),
            "include_archived": getattr(payload, "include_archived", False),
            "limit": limit,
        },
    }


def _conflict_search_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("term"),
        item.get("scope"),
        *(item.get("item_keys") or []),
        *(item.get("titles") or []),
    ]
    return " ".join(str(part or "").lower() for part in parts)


def _item(row: KnowledgeItem, *, conflicting_ids: set[int]) -> dict[str, Any]:
    draft_ready = _has_draft_content(row)
    published = row.published_version > 0 and row.status == "active"
    indexed = bool(row.indexed_version and row.indexed_version >= row.published_version and row.chunk_count > 0)
    has_conflict = row.id in conflicting_ids
    publish_ready = row.status != "archived" and draft_ready and not has_conflict
    return {
        "id": row.id,
        "item_key": row.item_key,
        "title": row.title,
        "status": row.status,
        "source_type": row.source_type,
        "knowledge_kind": row.knowledge_kind,
        "channel": row.channel,
        "audience_scope": row.audience_scope,
        "language": row.language,
        "priority": row.priority,
        "parsing_status": row.parsing_status,
        "fact_status": row.fact_status,
        "answer_mode": row.answer_mode,
        "published_version": row.published_version,
        "indexed_version": row.indexed_version,
        "chunk_count": row.chunk_count,
        "draft_ready": draft_ready,
        "publish_ready": publish_ready,
        "retrieval_test_ready": published and indexed,
        "has_conflict": has_conflict,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "href": "/ai-control",
        "evidence": "published chunks indexed" if indexed else "draft saved in knowledge_items",
    }


def build_knowledge_studio(db: Session, current_user) -> dict[str, Any]:
    now = utc_now()
    capabilities = resolve_capabilities(current_user, db)
    if not (capabilities & KNOWLEDGE_STUDIO_CAPABILITIES):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="knowledge_studio_requires_ai_config_capability")
    tenant_key = authoritative_tenant_key(
        db, current_user, allow_platform_default=True
    )

    rows = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.tenant_id == tenant_key)
        .order_by(KnowledgeItem.status.asc(), KnowledgeItem.priority.asc(), KnowledgeItem.updated_at.desc(), KnowledgeItem.item_key.asc())
        .limit(200)
        .all()
    )
    total = len(rows)
    draft_count = sum(1 for row in rows if row.status == "draft")
    active_count = sum(1 for row in rows if row.status == "active")
    archived_count = sum(1 for row in rows if row.status == "archived")
    published_count = sum(1 for row in rows if row.published_version > 0)
    ready_drafts = sum(1 for row in rows if row.status != "archived" and _has_draft_content(row))
    file_items = sum(1 for row in rows if row.source_type == "file")
    parsed_files = sum(1 for row in rows if row.source_type == "file" and row.parsing_status == "parsed")
    indexed_chunks = int(
        db.query(func.count(KnowledgeChunk.id))
        .filter(KnowledgeChunk.tenant_id == tenant_key).scalar() or 0
    )
    version_count = int(
        db.query(func.count(KnowledgeItemVersion.id))
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeItemVersion.item_id)
        .filter(KnowledgeItem.tenant_id == tenant_key).scalar() or 0
    )
    stale_index_count = sum(1 for row in rows if row.published_version > 0 and row.indexed_version < row.published_version)
    conflicts, conflicting_ids = _build_conflicts(rows)
    visible_conflicts = conflicts[:12]
    manage_enabled = CAP_AI_CONFIG_MANAGE in capabilities

    return {
        "generated_at": now.isoformat(),
        "role": _value(current_user.role),
        "user_id": current_user.id,
        "capabilities": sorted(capabilities),
        "kpis": [
            _kpi("total_items", "知识条目", total, "knowledge_items 中的真实条目总数", _tone(total, danger=100, warning=1)),
            _kpi("draft_items", "待发布草稿", draft_count, "status=draft，尚未进入运行时", _tone(draft_count, danger=10, warning=1)),
            _kpi("active_published", "已发布知识", published_count, "published_version > 0 的知识条目", _tone(published_count, danger=100, warning=1)),
            _kpi("indexed_chunks", "检索分段", indexed_chunks, "knowledge_chunks 中可被 retrieve-test 命中的分段", _tone(indexed_chunks, danger=500, warning=1)),
            _kpi("conflict_groups", "冲突组", len(conflicts), "同一 scope 下相同问题/别名的候选冲突", _tone(len(conflicts), danger=1, warning=1)),
            _kpi("stale_index", "索引滞后", stale_index_count, "published_version 已更新但 indexed_version 未追上", _tone(stale_index_count, danger=1, warning=1)),
        ],
        "items": [_item(row, conflicting_ids=conflicting_ids) for row in rows[:50]],
        "conflicts": visible_conflicts,
        "release_lifecycle": [
            _lifecycle_step("draft", "Draft", "Product / AI Ops", "KnowledgeItem draft_body, aliases, scope", "implemented", ready_drafts, "/ai-control", manage_enabled),
            _lifecycle_step("document-ingestion", "Document Ingestion", "AI Ops", "UploadFile -> parsed draft body", "implemented" if file_items else "linked", parsed_files, "/ai-control", manage_enabled),
            _lifecycle_step("retrieval-test", "Retrieval Test", "AI Ops", "POST /api/knowledge-items/retrieve-test", "implemented" if indexed_chunks else "linked", indexed_chunks, "/knowledge-studio", True),
            _lifecycle_step("conflict-scan", "Conflict Scan", "Product / Manager", "POST /api/knowledge-items/conflict-check", "implemented", len(conflicts), "/knowledge-studio", True),
            _lifecycle_step("golden-test", "Golden Test", "Product / QA", "POST /api/knowledge-items/golden-test", "implemented", indexed_chunks, "/knowledge-studio", True),
            _lifecycle_step("published", "Published", "AI Ops / Product", "KnowledgeItemVersion + KnowledgeChunk", "implemented" if published_count else "linked", published_count, "/ai-control", manage_enabled),
            _lifecycle_step("rollback", "Rollback", "AI Ops / Product", "POST /api/knowledge-items/{id}/rollback", "implemented" if version_count else "linked", version_count, "/ai-control", manage_enabled),
        ],
        "template_blocks": [
            _template_block("asset-library", "Asset Library", "GET /api/knowledge-items", "implemented", "读取真实 KnowledgeItem 列表、状态、scope、版本和索引字段", "/knowledge-studio"),
            _template_block("editor-draft", "Editor / Draft Save", "POST/PATCH /api/knowledge-items", "implemented", "草稿正文、FAQ/business_fact、别名、answer mode 均落到后端表", "/ai-control"),
            _template_block("document-upload", "Document Upload / Parse Preview", "POST /api/knowledge-items/upload and /{id}/upload", "implemented", "上传文件解析为 draft_body，并记录 file_storage_key/parsing_status", "/ai-control"),
            _template_block("retrieval-test", "Retrieval Test / Runtime Evidence", "POST /api/knowledge-items/retrieve-test", "implemented", "只检索 active published chunks，返回 score、matched_terms 和 grounding_source", "/knowledge-studio"),
            _template_block("publish-rollback", "Publish / Rollback", "POST /api/knowledge-items/{id}/publish|rollback", "implemented", "发布创建 KnowledgeItemVersion 和 KnowledgeChunk；回滚创建新发布版本", "/ai-control"),
            _template_block("conflict-scan", "Conflict Scan", "POST /api/knowledge-items/conflict-check", "implemented", "专用命令按 scope、问题和别名扫描冲突，并返回 blocker/evidence", "/knowledge-studio"),
            _template_block("golden-test", "Golden Test Command", "POST /api/knowledge-items/golden-test", "implemented", "基于已发布检索结果校验 expected source、expected answer、forbidden terms 和最低分", "/knowledge-studio"),
        ],
        "facts": {
            "draft_items": draft_count,
            "active_items": active_count,
            "archived_items": archived_count,
            "published_items": published_count,
            "ready_drafts": ready_drafts,
            "file_items": file_items,
            "parsed_files": parsed_files,
            "indexed_chunks": indexed_chunks,
            "version_count": version_count,
            "conflict_groups": len(conflicts),
            "stale_index_count": stale_index_count,
            "ai_config_read_capability": CAP_AI_CONFIG_READ in capabilities,
            "ai_config_manage_capability": CAP_AI_CONFIG_MANAGE in capabilities,
            "dedicated_conflict_check_endpoint": "implemented",
            "dedicated_golden_test_endpoint": "implemented",
        },
    }
