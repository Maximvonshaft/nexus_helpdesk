from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models_control_plane import KnowledgeChunk, KnowledgeItem
from ..utils.time import utc_now


SYNC_SCHEMA = "nexus.ai_runtime_rag_sync.v1"
DEFAULT_UPSERT_PATH = "/rag/upsert"


@dataclass(frozen=True)
class RuntimeRagSyncItem:
    external_id: str
    text: str
    title: str
    metadata: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "text": self.text,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class RuntimeRagSyncResult:
    ok: bool
    dry_run: bool
    selected_chunks: int
    selected_items: int
    upserted_chunks: int
    skipped_internal_chunks: int
    batches: int
    elapsed_ms: int
    endpoint_path: str
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "selected_chunks": self.selected_chunks,
            "selected_items": self.selected_items,
            "upserted_chunks": self.upserted_chunks,
            "skipped_internal_chunks": self.skipped_internal_chunks,
            "batches": self.batches,
            "elapsed_ms": self.elapsed_ms,
            "endpoint_path": self.endpoint_path,
            "error_code": self.error_code,
        }


def build_runtime_rag_sync_items(
    db: Session,
    *,
    item_key_prefix: str | None = None,
    include_internal: bool = False,
    limit: int | None = None,
) -> tuple[list[RuntimeRagSyncItem], int]:
    now = utc_now()
    query = (
        db.query(KnowledgeChunk, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.item_id)
        .filter(
            KnowledgeItem.status == "active",
            KnowledgeChunk.status == "active",
            KnowledgeItem.published_version > 0,
            KnowledgeChunk.published_version == KnowledgeItem.published_version,
            KnowledgeChunk.published_version > 0,
            KnowledgeItem.audience_scope == "customer",
            KnowledgeItem.visibility == "customer",
            KnowledgeChunk.visibility == "customer",
            KnowledgeItem.shareability.in_(("customer_visible", "runtime_context")),
            KnowledgeChunk.shareability.in_(("customer_visible", "runtime_context")),
            or_(KnowledgeItem.starts_at.is_(None), KnowledgeItem.starts_at <= now),
            or_(KnowledgeItem.ends_at.is_(None), KnowledgeItem.ends_at >= now),
            or_(KnowledgeItem.valid_from.is_(None), KnowledgeItem.valid_from <= now),
            or_(KnowledgeItem.valid_until.is_(None), KnowledgeItem.valid_until >= now),
            or_(KnowledgeChunk.starts_at.is_(None), KnowledgeChunk.starts_at <= now),
            or_(KnowledgeChunk.ends_at.is_(None), KnowledgeChunk.ends_at >= now),
            or_(KnowledgeChunk.valid_from.is_(None), KnowledgeChunk.valid_from <= now),
            or_(KnowledgeChunk.valid_until.is_(None), KnowledgeChunk.valid_until >= now),
        )
        .order_by(KnowledgeItem.priority.asc(), KnowledgeItem.item_key.asc(), KnowledgeChunk.chunk_index.asc())
    )
    if item_key_prefix:
        query = query.filter(KnowledgeItem.item_key.like(f"{item_key_prefix}%"))
    if limit:
        query = query.limit(max(1, int(limit)))

    items: list[RuntimeRagSyncItem] = []
    skipped_internal = 0
    for chunk, item in query.all():
        citation = item.citation_metadata_json or {}
        if not include_internal and citation.get("customer_visible") is False:
            skipped_internal += 1
            continue
        text = _sync_text(item, chunk)
        if not text.strip():
            continue
        metadata = _sync_metadata(item, chunk, text=text)
        items.append(
            RuntimeRagSyncItem(
                external_id=_external_id(chunk),
                title=item.title,
                text=text,
                metadata=metadata,
            )
        )
    return items, skipped_internal


def sync_runtime_rag(
    db: Session,
    *,
    base_url: str | None = None,
    token_file: str | None = None,
    token: str | None = None,
    upsert_path: str = DEFAULT_UPSERT_PATH,
    item_key_prefix: str | None = None,
    include_internal: bool = False,
    limit: int | None = None,
    batch_size: int = 64,
    timeout_seconds: int = 30,
    dry_run: bool = False,
) -> RuntimeRagSyncResult:
    started = time.monotonic()
    endpoint = _endpoint(base_url=base_url, upsert_path=upsert_path)
    sync_items, skipped_internal = build_runtime_rag_sync_items(
        db,
        item_key_prefix=item_key_prefix,
        include_internal=include_internal,
        limit=limit,
    )
    selected_item_keys = {item.metadata["item_key"] for item in sync_items}
    if dry_run:
        return RuntimeRagSyncResult(
            ok=True,
            dry_run=True,
            selected_chunks=len(sync_items),
            selected_items=len(selected_item_keys),
            upserted_chunks=0,
            skipped_internal_chunks=skipped_internal,
            batches=0,
            elapsed_ms=_elapsed_ms(started),
            endpoint_path=upsert_path,
        )

    runtime_token = _read_token(token_file=token_file, inline_token=token)
    if not endpoint:
        return _failure(started, upsert_path=upsert_path, error_code="ai_runtime_rag_sync_base_url_missing", selected=len(sync_items), skipped=skipped_internal)
    if not runtime_token:
        return _failure(started, upsert_path=upsert_path, error_code="ai_runtime_rag_sync_token_missing", selected=len(sync_items), skipped=skipped_internal)

    upserted = 0
    batches = 0
    try:
        for offset in range(0, len(sync_items), max(1, min(batch_size, 256))):
            batch = sync_items[offset : offset + max(1, min(batch_size, 256))]
            if not batch:
                continue
            payload = {"items": [item.as_payload() for item in batch]}
            _post_json(endpoint, payload, runtime_token, timeout_seconds=timeout_seconds)
            upserted += len(batch)
            batches += 1
    except urllib.error.HTTPError as exc:
        return _failure(started, upsert_path=upsert_path, error_code=f"ai_runtime_rag_sync_http_{exc.code}", selected=len(sync_items), skipped=skipped_internal, upserted=upserted, batches=batches)
    except urllib.error.URLError:
        return _failure(started, upsert_path=upsert_path, error_code="ai_runtime_rag_sync_url_error", selected=len(sync_items), skipped=skipped_internal, upserted=upserted, batches=batches)
    except (TimeoutError, OSError, ValueError) as exc:
        return _failure(started, upsert_path=upsert_path, error_code=f"ai_runtime_rag_sync_{exc.__class__.__name__}", selected=len(sync_items), skipped=skipped_internal, upserted=upserted, batches=batches)

    _mark_synced_items(db, sync_items)
    return RuntimeRagSyncResult(
        ok=True,
        dry_run=False,
        selected_chunks=len(sync_items),
        selected_items=len(selected_item_keys),
        upserted_chunks=upserted,
        skipped_internal_chunks=skipped_internal,
        batches=batches,
        elapsed_ms=_elapsed_ms(started),
        endpoint_path=upsert_path,
    )


def _sync_text(item: KnowledgeItem, chunk: KnowledgeChunk) -> str:
    if (item.knowledge_kind or "").strip() in {"business_fact", "faq"} and item.fact_question and item.fact_answer:
        aliases = " ".join(f"Alias: {alias}" for alias in (item.fact_aliases_json or []) if str(alias).strip())
        return "\n\n".join(
            part
            for part in [
                f"Title: {item.title}",
                f"Kind: {item.knowledge_kind}",
                f"Question: {item.fact_question}",
                aliases,
                f"Answer: {item.fact_answer}",
            ]
            if part
        )
    return "\n\n".join(
        part
        for part in [
            f"Title: {item.title}",
            f"Kind: {item.knowledge_kind or 'document'}",
            chunk.chunk_text or chunk.normalized_text or "",
        ]
        if part
    )


def _sync_metadata(item: KnowledgeItem, chunk: KnowledgeChunk, *, text: str) -> dict[str, Any]:
    citation = item.citation_metadata_json or {}
    retrieval = chunk.retrieval_metadata_json or {}
    source_external_id = _source_external_id(chunk)
    return {
        "schema": SYNC_SCHEMA,
        "source": "nexus",
        "external_id": _external_id(chunk),
        "source_external_id": source_external_id,
        "item_id": item.id,
        "item_key": item.item_key,
        "published_version": item.published_version,
        "chunk_index": chunk.chunk_index,
        "title": item.title,
        "knowledge_kind": item.knowledge_kind,
        "audience_scope": item.audience_scope,
        "tenant_id": chunk.tenant_id or item.tenant_id,
        "brand_id": chunk.brand_id or item.brand_id,
        "country_scope": (chunk.country_scope or item.country_scope or "GLOBAL").upper(),
        "channel_scope": chunk.channel_scope or item.channel_scope or "all",
        "locale": chunk.locale or item.locale or item.language,
        "visibility": chunk.visibility or item.visibility,
        "shareability": chunk.shareability or item.shareability,
        "authority_level": chunk.authority_level or item.authority_level,
        "risk_level": chunk.risk_level or item.risk_level,
        "valid_from": (chunk.valid_from or item.valid_from).isoformat() if (chunk.valid_from or item.valid_from) else None,
        "valid_until": (chunk.valid_until or item.valid_until).isoformat() if (chunk.valid_until or item.valid_until) else None,
        "channel": item.channel,
        "language": item.language,
        "priority": item.priority,
        "content_hash": chunk.content_hash,
        "semantic_hash": chunk.semantic_hash or hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
        "source_document_id": chunk.source_document_id or retrieval.get("source_document_id") or item.file_storage_key or item.item_key,
        "source_path": citation.get("source_path") or item.file_name,
        "customer_visible": citation.get("customer_visible", True),
        "citation": citation,
    }


def _mark_synced_items(db: Session, sync_items: list[RuntimeRagSyncItem]) -> None:
    by_item_key: dict[str, list[RuntimeRagSyncItem]] = {}
    for item in sync_items:
        by_item_key.setdefault(str(item.metadata["item_key"]), []).append(item)
    if not by_item_key:
        return
    rows = db.query(KnowledgeItem).filter(KnowledgeItem.item_key.in_(tuple(by_item_key))).all()
    now = utc_now().isoformat()
    for row in rows:
        chunks = by_item_key.get(row.item_key, [])
        metadata = dict(row.citation_metadata_json or {})
        metadata["ai_runtime_rag_sync"] = {
            "schema": SYNC_SCHEMA,
            "status": "synced",
            "synced_at": now,
            "published_version": row.published_version,
            "chunk_count": len(chunks),
            "external_ids": [item.external_id for item in chunks[:20]],
            "external_id_count": len(chunks),
        }
        row.citation_metadata_json = metadata
    db.flush()


def _external_id(chunk: KnowledgeChunk) -> str:
    digest = hashlib.sha256(_source_external_id(chunk).encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"nexus:{digest}"


def _source_external_id(chunk: KnowledgeChunk) -> str:
    return f"nexus:{chunk.item_key}:v{chunk.published_version}:c{chunk.chunk_index}"


def _endpoint(*, base_url: str | None, upsert_path: str) -> str | None:
    url = (base_url or os.getenv("PRIVATE_AI_RUNTIME_BASE_URL") or "").strip().rstrip("/")
    if not url:
        return None
    return urljoin(f"{url}/", (upsert_path or DEFAULT_UPSERT_PATH).lstrip("/"))


def _read_token(*, token_file: str | None, inline_token: str | None) -> str | None:
    value = (inline_token or "").strip()
    path = (token_file or os.getenv("PRIVATE_AI_RUNTIME_TOKEN_FILE") or "").strip()
    if not value and path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    return value or None


def _post_json(endpoint: str, payload: dict[str, Any], token: str, *, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
        decoded = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(decoded, dict):
        raise ValueError("ai_runtime_rag_sync_response_not_object")
    return decoded


def _failure(
    started: float,
    *,
    upsert_path: str,
    error_code: str,
    selected: int,
    skipped: int,
    upserted: int = 0,
    batches: int = 0,
) -> RuntimeRagSyncResult:
    return RuntimeRagSyncResult(
        ok=False,
        dry_run=False,
        selected_chunks=selected,
        selected_items=0,
        upserted_chunks=upserted,
        skipped_internal_chunks=skipped,
        batches=batches,
        elapsed_ms=_elapsed_ms(started),
        endpoint_path=upsert_path,
        error_code=error_code,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
