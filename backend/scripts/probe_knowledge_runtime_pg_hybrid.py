from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models, models_control_plane  # noqa: F401,E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.models_control_plane import KnowledgeChunk, KnowledgeItem  # noqa: E402
from app.services.knowledge_retrieval_service import index_published_item  # noqa: E402
from app.services.knowledge_runtime import retrieve_knowledge  # noqa: E402
from app.services.knowledge_runtime.embeddings import get_embedding_provider, semantic_hash, vector_literal  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


class _EmbeddingHandler(BaseHTTPRequestHandler):
    server_version = "NexusHybridEmbeddingProbe/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        inputs = payload.get("input") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        data = [
            {"index": index, "embedding": _probe_embedding(str(value), dim=1024)}
            for index, value in enumerate(inputs)
        ]
        body = json.dumps({"object": "list", "data": data, "model": payload.get("model")}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe production-like Knowledge Runtime v2 on PostgreSQL FTS + pgvector.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must be PostgreSQL for the pg hybrid probe")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ["KNOWLEDGE_EMBEDDINGS_ENABLED"] = "true"
    os.environ["KNOWLEDGE_EMBEDDING_PROVIDER"] = "openai_compatible"
    os.environ["KNOWLEDGE_EMBEDDING_MODEL"] = "probe-openai-compatible-1024"
    os.environ["KNOWLEDGE_EMBEDDING_DIM"] = "1024"
    os.environ["KNOWLEDGE_EMBEDDING_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
    os.environ["KNOWLEDGE_EMBEDDING_API_KEY"] = "probe-only-not-a-secret"
    os.environ["KNOWLEDGE_VECTOR_FALLBACK_ALLOWED"] = "false"
    get_settings.cache_clear()

    engine = create_engine(args.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("probe_database_not_postgresql")
        _assert_pgvector_schema(db)
        _seed_probe_knowledge(db)
        _embed_active_chunks(db)
        result = retrieve_knowledge(
            db,
            query="Can I change the delivery address before dispatch?",
            tenant_key="default",
            channel="website",
            audience_scope="customer",
            language="en",
            limit=5,
        )
        payload = _assert_result(result)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    finally:
        db.close()
        engine.dispose()
        server.shutdown()
        server.server_close()


def _assert_pgvector_schema(db) -> None:
    vector_installed = db.execute(text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'")).scalar()
    if int(vector_installed or 0) < 1:
        raise RuntimeError("pgvector_extension_missing")
    column = db.execute(
        text(
            """
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding_vector'
            """
        )
    ).scalar()
    if column != "vector":
        raise RuntimeError(f"knowledge_chunks.embedding_vector_not_vector:{column}")


def _seed_probe_knowledge(db) -> None:
    suffix = hashlib.sha1(os.urandom(16)).hexdigest()[:10]
    user = User(
        username=f"pg-hybrid-probe-{suffix}",
        display_name="Hybrid Probe",
        email=f"pg-hybrid-probe-{suffix}@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    db.flush()
    now = utc_now()
    item = KnowledgeItem(
        item_key=f"pg_hybrid.address_change.{suffix}",
        title="Speedaf Address Change Production Policy",
        summary="Address changes are allowed before dispatch only.",
        status="active",
        source_type="text",
        knowledge_kind="business_fact",
        channel="website",
        audience_scope="customer",
        language="en",
        priority=1,
        fact_question="Can a customer change the delivery address?",
        fact_answer="Customers may request a delivery address change before dispatch; after dispatch, support must hand off for manual verification.",
        fact_aliases_json=["address change", "change delivery address", "before dispatch"],
        fact_status="approved",
        answer_mode="direct_answer",
        citation_metadata_json={"source": "pg_hybrid_probe", "version": "2026-06-01"},
        published_body="Customers may request a delivery address change before dispatch; after dispatch, support must hand off for manual verification.",
        published_normalized_text="customers may request delivery address change before dispatch after dispatch support must hand off manual verification",
        published_version=1,
        published_at=now,
        created_by=user.id,
        updated_by=user.id,
        published_by=user.id,
    )
    db.add(item)
    db.flush()
    index_published_item(db, item)
    db.commit()


def _embed_active_chunks(db) -> None:
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
    chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.status == "active").order_by(KnowledgeChunk.id.asc()).all()
    vectors = provider.embed_texts([chunk.normalized_text or chunk.chunk_text for chunk in chunks])
    for chunk, vector in zip(chunks, vectors):
        text_value = chunk.normalized_text or chunk.chunk_text
        chunk.embedding = vector
        chunk.embedding_vector = vector_literal(vector)
        chunk.embedding_model = settings.knowledge_embedding_model
        chunk.embedding_dim = len(vector)
        chunk.embedding_status = "embedded"
        chunk.embedding_error = None
        chunk.embedded_at = utc_now()
        chunk.semantic_hash = semantic_hash(text_value)
        db.execute(
            text("UPDATE knowledge_chunks SET embedding_vector = CAST(:vector AS vector) WHERE id = :id"),
            {"id": chunk.id, "vector": vector_literal(vector)},
        )
    db.commit()


def _assert_result(result) -> dict[str, Any]:
    trace = result.trace
    methods = set(trace.get("retrieval_methods") or [])
    vector = trace.get("vector") or {}
    top_keys = [hit.item_key for hit in result.hits[:5]]
    if trace.get("retrieval") != "hybrid_rag":
        raise RuntimeError("retrieval_trace_not_hybrid_rag")
    if vector.get("provider") != "openai_compatible":
        raise RuntimeError("embedding_provider_not_openai_compatible")
    if vector.get("storage") != "pgvector":
        raise RuntimeError("vector_storage_not_pgvector")
    if vector.get("fallback_allowed") is not False:
        raise RuntimeError("vector_fallback_not_fail_closed")
    if "postgres_fts" not in methods:
        raise RuntimeError("postgres_fts_missing_from_trace")
    if "pgvector" not in methods:
        raise RuntimeError("pgvector_missing_from_trace")
    if not any(key.startswith("pg_hybrid.address_change.") for key in top_keys):
        raise RuntimeError(f"seeded_policy_not_retrieved:{top_keys}")
    if not result.direct_facts:
        raise RuntimeError("direct_fact_missing")
    return {
        "ok": True,
        "retrieval": trace.get("retrieval"),
        "retrieval_methods": sorted(methods),
        "vector": vector,
        "top_item_keys": top_keys,
        "direct_fact_count": len(result.direct_facts),
        "latency_ms": result.latency_ms,
    }


def _probe_embedding(text_value: str, *, dim: int) -> list[float]:
    vector = [0.0] * dim
    tokens = [token.strip().lower() for token in text_value.replace("-", " ").replace(";", " ").split() if len(token.strip()) > 1]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = sum(value * value for value in vector) ** 0.5 or 1.0
    return [round(value / norm, 6) for value in vector]


if __name__ == "__main__":
    raise SystemExit(main())
