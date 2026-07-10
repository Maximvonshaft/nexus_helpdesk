from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models_control_plane import KNOWLEDGE_VECTOR_DIMENSION, KnowledgeChunk  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.knowledge_runtime_v2.embeddings import (  # noqa: E402
    get_embedding_provider,
    semantic_hash,
    vector_literal,
)
from app.services.knowledge_runtime_v2.runtime import (  # noqa: E402
    KnowledgeVectorContractError,
    validate_embedding_batch,
    validate_embedding_dimension,
    validate_knowledge_vector,
)
from app.utils.time import utc_now  # noqa: E402


def _valid_existing_embedding(row: Any, *, model: str, current_hash: str) -> bool:
    if row.semantic_hash != current_hash:
        return False
    if row.embedding_model != model:
        return False
    if row.embedding_dim != KNOWLEDGE_VECTOR_DIMENSION:
        return False
    if row.embedding_status != "embedded":
        return False
    if row.embedding_vector in (None, ""):
        return False
    try:
        validate_knowledge_vector(row.embedding)
    except KnowledgeVectorContractError:
        return False
    return True


def _mark_failed(rows: list[Any], reason: str) -> None:
    safe_reason = str(reason or "embedding_failed")[:120]
    for row in rows:
        row.embedding_status = "failed"
        row.embedding_error = safe_reason


def _write_postgres_vector(db: Any, *, row_id: int, vector: list[float]) -> None:
    db.execute(
        text(
            "UPDATE knowledge_chunks "
            "SET embedding_vector = CAST(:vector AS vector(384)) "
            "WHERE id = :id"
        ),
        {"id": row_id, "vector": vector_literal(vector)},
    )


def run_backfill(
    db: Any,
    provider: Any,
    settings: Any,
    *,
    dry_run: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    validate_embedding_dimension(settings.knowledge_embedding_dim)
    batch_size = int(settings.knowledge_embedding_batch_size)
    if batch_size < 1:
        raise ValueError("knowledge_embedding_batch_size_invalid")

    query = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.status == "active")
        .order_by(KnowledgeChunk.id.asc())
    )
    if limit:
        query = query.limit(limit)
    rows = query.all()

    processed = embedded = skipped = failed = 0
    dialect = db.get_bind().dialect.name

    for offset in range(0, len(rows), batch_size):
        pending: list[tuple[Any, str]] = []
        for row in rows[offset : offset + batch_size]:
            processed += 1
            current_hash = semantic_hash(row.normalized_text or row.chunk_text)
            if _valid_existing_embedding(
                row,
                model=settings.knowledge_embedding_model,
                current_hash=current_hash,
            ):
                skipped += 1
                continue
            pending.append((row, current_hash))

        if not pending:
            continue

        texts = [row.normalized_text or row.chunk_text for row, _hash in pending]
        pending_rows = [row for row, _hash in pending]
        try:
            vectors = validate_embedding_batch(
                provider.embed_texts(texts),
                expected_count=len(pending),
            )
        except KnowledgeVectorContractError as exc:
            failed += len(pending)
            _mark_failed(pending_rows, str(exc))
            if not dry_run:
                db.commit()
            continue
        except Exception as exc:
            failed += len(pending)
            _mark_failed(pending_rows, type(exc).__name__)
            if not dry_run:
                db.commit()
            continue

        for (row, current_hash), vector in zip(pending, vectors):
            row.embedding = vector
            row.embedding_model = settings.knowledge_embedding_model
            row.embedding_dim = KNOWLEDGE_VECTOR_DIMENSION
            row.embedding_status = "embedded"
            row.embedding_error = None
            row.embedded_at = utc_now()
            row.semantic_hash = current_hash

            if dialect == "postgresql":
                _write_postgres_vector(db, row_id=row.id, vector=vector)
            else:
                row.embedding_vector = vector_literal(vector)
            embedded += 1

        if not dry_run:
            db.commit()

    if dry_run:
        db.rollback()

    return {
        "ok": failed == 0,
        "processed": processed,
        "embedded": embedded,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "dimension": KNOWLEDGE_VECTOR_DIMENSION,
        "storage": "pgvector" if dialect == "postgresql" else "text_fallback",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Knowledge Runtime v2 chunk embeddings."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
    try:
        validate_embedding_dimension(settings.knowledge_embedding_dim)
    except KnowledgeVectorContractError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": str(exc),
                    "configured_dimension": settings.knowledge_embedding_dim,
                    "required_dimension": KNOWLEDGE_VECTOR_DIMENSION,
                    "dry_run": args.dry_run,
                },
                sort_keys=True,
            )
        )
        return 1

    provider = get_embedding_provider(
        settings.knowledge_embedding_provider,
        dim=settings.knowledge_embedding_dim,
        model=settings.knowledge_embedding_model,
        base_url=settings.knowledge_embedding_base_url,
        api_key=settings.knowledge_embedding_api_key,
        api_key_file=settings.knowledge_embedding_api_key_file,
        timeout_seconds=settings.knowledge_embedding_timeout_seconds,
    )
    db = SessionLocal()
    try:
        result = run_backfill(
            db,
            provider,
            settings,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
