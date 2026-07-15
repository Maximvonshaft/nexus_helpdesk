from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app import models, models_control_plane  # noqa: F401,E402
from app.enums import UserRole  # noqa: E402
from app.models import User  # noqa: E402
from app.models_control_plane import KnowledgeItem  # noqa: E402
from app.models_control_plane import KnowledgeChunk  # noqa: E402
from app.services.knowledge_retrieval_service import index_published_item  # noqa: E402
from app.services.knowledge_runtime import retrieve_knowledge  # noqa: E402
from app.services.knowledge_runtime.embeddings import get_embedding_provider, semantic_hash, vector_literal  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Knowledge Runtime v2 production eval cases.")
    parser.add_argument("--cases", default=str(ROOT / "tests" / "fixtures" / "knowledge_eval_cases.json"))
    parser.add_argument("--min-recall-at-5", type=float, default=0.95)
    parser.add_argument("--max-hallucination-rate", type=float, default=0.0)
    parser.add_argument("--max-unsupported-answer-rate", type=float, default=0.05)
    parser.add_argument("--min-handoff-correctness", type=float, default=0.95)
    parser.add_argument("--min-vector-evidence-rate", type=float, default=0.5)
    parser.add_argument("--max-p95-latency-ms", type=int, default=250)
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="knowledge-eval-") as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'eval.db'}", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        db = SessionLocal()
        try:
            _seed_eval_knowledge(db)
            metrics = _run_cases(db, cases)
        finally:
            db.close()
            engine.dispose()

    ok = (
        metrics["recall_at_5"] >= args.min_recall_at_5
        and metrics["hallucination_rate"] <= args.max_hallucination_rate
        and metrics["unsupported_answer_rate"] <= args.max_unsupported_answer_rate
        and metrics["handoff_correctness"] >= args.min_handoff_correctness
        and metrics["vector_evidence_rate"] >= args.min_vector_evidence_rate
        and metrics["p95_latency_ms"] <= args.max_p95_latency_ms
    )
    payload = {"ok": ok, "thresholds": vars(args), "metrics": metrics}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if ok else 1


def _seed_eval_knowledge(db) -> None:
    user = User(username="eval-admin", display_name="Eval Admin", email="eval@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(user)
    db.flush()
    now = utc_now()
    rows = [
        KnowledgeItem(
            item_key="eval.address_change",
            title="Speedaf Address Change Policy",
            summary="Address changes are allowed before dispatch.",
            status="active",
            source_type="text",
            knowledge_kind="business_fact",
            channel="website",
            audience_scope="customer",
            language="zh",
            priority=1,
            fact_question="客户如何改地址？",
            fact_answer="客户可以在包裹发出前申请改地址；发出后需要转人工核实。",
            fact_aliases_json=["改地址", "地址变更", "change address"],
            fact_status="approved",
            answer_mode="direct_answer",
            citation_metadata_json={"source": "eval_policy", "version": "2026-06-01"},
            published_body="客户可以在包裹发出前申请改地址；发出后需要转人工核实。",
            published_normalized_text="客户 可以 在 包裹 发出 前 申请 改地址 发出 后 需要 转 人工 核实",
            published_version=1,
            published_at=now,
        ),
        KnowledgeItem(
            item_key="eval.delivery_sla",
            title="Speedaf Delivery SLA",
            summary="Delivery SLA after dispatch.",
            status="active",
            source_type="text",
            knowledge_kind="business_fact",
            channel="website",
            audience_scope="customer",
            language="en",
            priority=2,
            fact_question="How long does delivery take after dispatch?",
            fact_answer="After dispatch, standard delivery usually takes 3-5 business days, subject to route and customs conditions.",
            fact_aliases_json=["delivery time", "sla", "after dispatch"],
            fact_status="approved",
            answer_mode="direct_answer",
            citation_metadata_json={"source": "eval_sla", "version": "2026-06-01"},
            published_body="After dispatch, standard delivery usually takes 3-5 business days, subject to route and customs conditions.",
            published_normalized_text="after dispatch standard delivery usually takes 3-5 business days subject to route and customs conditions",
            published_version=1,
            published_at=now,
        ),
    ]
    for row in rows:
        row.created_by = user.id
        row.updated_by = user.id
        row.published_by = user.id
        db.add(row)
        db.flush()
        index_published_item(db, row)
    _embed_eval_chunks(db)
    db.commit()


def _embed_eval_chunks(db) -> None:
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
        chunk.embedding = vector
        chunk.embedding_vector = vector_literal(vector)
        chunk.embedding_model = settings.knowledge_embedding_model
        chunk.embedding_dim = len(vector)
        chunk.embedding_status = "embedded"
        chunk.embedding_error = None
        chunk.embedded_at = utc_now()
        chunk.semantic_hash = semantic_hash(chunk.normalized_text or chunk.chunk_text)


def _run_cases(db, cases: list[dict]) -> dict:
    supported_cases = unsupported_cases = expected_handoff_cases = 0
    recall_hits = direct_correct = unsupported_correct = handoff_correct = hallucinated = 0
    latencies: list[int] = []
    case_results = []
    vector_evidence_cases = 0
    for case in cases:
        started = time.monotonic()
        result = retrieve_knowledge(
            db,
            query=case["query"],
            tenant_key="default",
            channel="website",
            audience_scope="customer",
            language=case.get("language"),
            limit=5,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        latencies.append(latency_ms)
        hit_keys = [hit.item_key for hit in result.hits[:5]]
        supported = bool(case.get("expected_supported"))
        expected_handoff = bool(case.get("expected_handoff"))
        if supported:
            supported_cases += 1
            if case.get("expected_item_key") in hit_keys:
                recall_hits += 1
            if result.trace.get("candidates_by_source", {}).get("vector", 0) > 0:
                vector_evidence_cases += 1
            if any((case.get("expected_answer_contains") or "") in (hit.direct_answer or hit.text) for hit in result.hits):
                direct_correct += 1
        else:
            unsupported_cases += 1
            if result.no_answer_reason == "no_evidence" or not result.hits:
                unsupported_correct += 1
            if case.get("tracking_truth_boundary") and any(_looks_like_live_tracking_status(hit) for hit in result.hits):
                hallucinated += 1
        if expected_handoff:
            expected_handoff_cases += 1
            if result.no_answer_reason == "no_evidence" or not result.hits:
                handoff_correct += 1
        case_results.append({"id": case["id"], "hit_keys": hit_keys, "no_answer_reason": result.no_answer_reason, "latency_ms": latency_ms, "trace": result.trace})
    recall_at_5 = recall_hits / supported_cases if supported_cases else 1.0
    direct_answer_correctness = direct_correct / supported_cases if supported_cases else 1.0
    unsupported_answer_rate = 1.0 - (unsupported_correct / unsupported_cases if unsupported_cases else 1.0)
    hallucination_rate = hallucinated / len(cases) if cases else 0.0
    handoff_correctness = handoff_correct / expected_handoff_cases if expected_handoff_cases else 1.0
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))] if sorted_latencies else 0
    return {
        "case_count": len(cases),
        "recall_at_5": round(recall_at_5, 4),
        "direct_answer_correctness": round(direct_answer_correctness, 4),
        "unsupported_answer_rate": round(unsupported_answer_rate, 4),
        "hallucination_rate": round(hallucination_rate, 4),
        "handoff_correctness": round(handoff_correctness, 4),
        "vector_evidence_rate": round(vector_evidence_cases / supported_cases if supported_cases else 1.0, 4),
        "p95_latency_ms": p95,
        "cases": case_results,
    }


def _looks_like_live_tracking_status(hit: SimpleNamespace) -> bool:
    text_value = f"{hit.direct_answer or ''} {hit.text or ''}".lower()
    live_terms = ("delivered", "in transit", "out for delivery", "已签收", "运输中", "派送中", "到达")
    return any(term in text_value for term in live_terms)


if __name__ == "__main__":
    raise SystemExit(main())
