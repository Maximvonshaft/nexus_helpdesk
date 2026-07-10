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
from app.models_control_plane import KnowledgeChunk  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.knowledge_runtime_v2.embeddings import (  # noqa: E402
    get_embedding_provider,
    semantic_hash,
    vector_literal,
)
from app.services.knowledge_runtime_v2.vector_contract import (  # noqa: E402
    embedding_is_current,
    validate_embedding_dimension,
    validate_embedding_vector,
)
from app.utils.time import utc_now  # noqa: E402


def _mark_failed(batch: list[tuple[Any, str]], exc: Exception) -> None:
    reason = type(exc).__name__
    for row, _current_hash in batch:
        row.embedding_status = "failed"
        row.embedding_error = reason[:120]


def run_backfill(
    *,
    db: Any,
    provider: Any,
    settings: Any,
    dry_run: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    expected_dim = validate_embedding_dimension(settings.knowledge_embedding_dim)
    dialect_name = db.get_bind().dialect.name
    processed = embedded = skipped = failed = 0

    query = db.query(KnowledgeChunk).filter(KnowledgeChunk.status == "active").order_by(KnowledgeChunk.id.asc())
    if limit:
        query = query.limit(limit)
    rows = query.all()
    batch_size = max(1, int(settings.knowledge_embedding_batch_size))

    for offset in range(0, len(rows), batch_size):
        batch: list[tuple[Any, str]] = []
        for row in rows[offset : offset + batch_size]:
            processed += 1
            current_hash = semantic_hash(row.normalized_text or row.chunk_text)
            if embedding_is_current(
                row,
                semantic_hash=current_hash,
                model=settings.knowledge_embedding_model,
                expected_dim=expected_dim,
            ):
                skipped += 1
                continue
            batch.append((row, current_hash))
        if not batch:
            continue

        texts = [row.normalized_text or row.chunk_text for row, _hash in batch]
        try:
            vectors = list(provider.embed_texts(texts))
            if len(vectors) != len(batch):
                raise ValueError("knowledge_embedding_provider_cardinality_mismatch")
            normalized_vectors = [
                validate_embedding_vector(vector, expected_dim=expected_dim)
                for vector in vectors
            ]
        except Exception as exc:
            failed += len(batch)
            _mark_failed(batch, exc)
            if not dry_run:
                db.commit()
            continue

        for (row, current_hash), vector in zip(batch, normalized_vectors, strict=True):
            row.embedding = vector
            row.embedding_model = settings.knowledge_embedding_model
            row.embedding_dim = expected_dim
            row.embedding_status = "embedded"
            row.embedding_error = None
            row.embedded_at = utc_now()
            row.semantic_hash = current_hash
            if dialect_name == "postgresql":
                # pgvector is written once through an explicit typed cast; do not make
                # SQLAlchemy bind a textual vector through the ORM UserDefinedType.
                row.embedding_vector = None
                db.execute(
                    text(
                        "UPDATE knowledge_chunks "
                        "SET embedding_vector = CAST(:vector AS vector) "
                        "WHERE id = :id"
                    ),
                    {"id": row.id, "vector": vector_literal(vector)},
                )
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
        "embedding_dim": expected_dim,
        "storage": "pgvector" if dialect_name == "postgresql" else "json_text_fallback",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Knowledge Runtime v2 chunk embeddings.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
    validate_embedding_dimension(settings.knowledge_embedding_dim)
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
        report = run_backfill(
            db=db,
            provider=provider,
            settings=settings,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
