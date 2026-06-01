from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models_control_plane import KnowledgeChunk  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.services.knowledge_runtime_v2.embeddings import get_embedding_provider, semantic_hash, vector_literal  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Knowledge Runtime v2 chunk embeddings.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
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
    processed = embedded = skipped = failed = 0
    try:
        query = db.query(KnowledgeChunk).filter(KnowledgeChunk.status == "active").order_by(KnowledgeChunk.id.asc())
        if args.limit:
            query = query.limit(args.limit)
        rows = query.all()
        batch_size = settings.knowledge_embedding_batch_size
        for offset in range(0, len(rows), batch_size):
            batch = []
            for row in rows[offset:offset + batch_size]:
                processed += 1
                current_hash = semantic_hash(row.normalized_text or row.chunk_text)
                if row.semantic_hash == current_hash and row.embedding and row.embedding_model == settings.knowledge_embedding_model:
                    skipped += 1
                    continue
                batch.append((row, current_hash))
            if not batch:
                continue
            texts = [row.normalized_text or row.chunk_text for row, _hash in batch]
            try:
                vectors = provider.embed_texts(texts)
            except Exception as exc:
                failed += len(batch)
                for row, _hash in batch:
                    row.embedding_status = "failed"
                    row.embedding_error = type(exc).__name__
                if not args.dry_run:
                    db.commit()
                continue
            for (row, current_hash), vector in zip(batch, vectors):
                row.embedding = vector
                row.embedding_vector = vector_literal(vector)
                row.embedding_model = settings.knowledge_embedding_model
                row.embedding_dim = len(vector)
                row.embedding_status = "embedded"
                row.embedding_error = None
                row.embedded_at = utc_now()
                row.semantic_hash = current_hash
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(
                        text("UPDATE knowledge_chunks SET embedding_vector = CAST(:vector AS vector) WHERE id = :id"),
                        {"id": row.id, "vector": vector_literal(vector)},
                    )
                embedded += 1
            if not args.dry_run:
                db.commit()
        if args.dry_run:
            db.rollback()
        print(json.dumps({"ok": failed == 0, "processed": processed, "embedded": embedded, "skipped": skipped, "failed": failed, "dry_run": args.dry_run}, sort_keys=True))
        return 0 if failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
