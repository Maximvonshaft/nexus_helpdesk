#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.domain_intelligence.flags import DomainRuntimeFlags  # noqa: E402
from app.services.domain_intelligence.webchat_shadow_bridge import build_webchat_domain_shadow_trace  # noqa: E402
from app.services.knowledge_prompt_service import summarize_rag_trace  # noqa: E402
from app.services.webchat_runtime_ai_service import _attach_domain_shadow_trace  # noqa: E402


SAMPLES = [
    {
        "id": "greeting_default",
        "query": "hello",
        "expected_intent": None,
        "expected_plan": "safe_general_reply",
    },
    {
        "id": "tracking_tool_boundary",
        "query": "Where is my parcel now?",
        "expected_intent": "logistics.tracking_status",
        "expected_plan": "tool_call",
    },
    {
        "id": "missed_delivery_policy",
        "query": "The courier arrived while I was not home. Will you deliver again?",
        "expected_intent": "logistics.delivery_attempt_failed.recipient_absent",
        "expected_plan": "guided_answer",
    },
    {
        "id": "address_change_verification",
        "query": "地址写错了，可以改地址吗？",
        "expected_intent": "logistics.address_change",
        "expected_plan": "tool_prepare",
    },
    {
        "id": "complaint_work_order_boundary",
        "query": "I want to complain about the courier.",
        "expected_intent": "logistics.complaint_escalation",
        "expected_plan": "work_order_create",
    },
]


def _base_runtime_context() -> dict[str, Any]:
    return {
        "context_version": "nexus_webchat_runtime_context_v2",
        "tenant_key": "default",
        "knowledge_context": {
            "retrieval": "hybrid_rag_v2",
            "total_matches": 0,
            "candidate_count": 0,
            "query_analysis": {"terms": []},
            "hits": [],
            "locked_facts": [],
            "evidence_pack": [],
        },
        "rag_trace": {"retrieval": "hybrid_rag_v2", "top_hits": []},
        "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _verify_side_effect_contract(trace: dict[str, Any]) -> None:
    side_effects = trace.get("side_effects")
    _assert(isinstance(side_effects, dict), "side_effects must be present")
    for key in ("tool_executed", "ticket_created", "handoff_triggered", "reply_changed", "retrieval_changed"):
        _assert(side_effects.get(key) is False, f"{key} must be false")


def run_probe() -> dict[str, Any]:
    started = time.time()
    original_env = os.environ.get("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED")
    results: list[dict[str, Any]] = []
    try:
        os.environ.pop("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", None)
        flags = DomainRuntimeFlags.from_env()
        _assert(flags.webchat_shadow_trace_enabled is False, "webchat shadow trace must be off by default")
        default_trace = build_webchat_domain_shadow_trace(
            body="Where is my parcel now?",
            tenant_key="default",
            channel_key="website",
        )
        _assert(default_trace is None, "default disabled trace must be None")
        default_context = _base_runtime_context()
        default_attached = _attach_domain_shadow_trace(
            default_context,
            body="Where is my parcel now?",
            tenant_key="default",
            channel_key="website",
            market_id=None,
            language="en",
        )
        _assert(default_attached is default_context, "default disabled context must be returned unchanged")
        _assert("domain_intelligence_trace" not in default_context, "default context must not include domain_intelligence_trace")
        results.append({"id": "default_flag_off", "ok": True})

        os.environ["DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED"] = "true"
        flags = DomainRuntimeFlags.from_env()
        _assert(flags.webchat_shadow_trace_enabled is True, "webchat shadow trace flag must be enabled")

        for sample in SAMPLES:
            trace = build_webchat_domain_shadow_trace(
                body=sample["query"],
                tenant_key="default",
                channel_key="website",
                language="en",
            )
            _assert(isinstance(trace, dict), f"{sample['id']} trace must be dict")
            _assert(trace.get("trace_version") == "domain_webchat_shadow_trace_v1", f"{sample['id']} trace version mismatch")
            _assert(trace.get("shadow_mode") is True, f"{sample['id']} must be shadow")
            _assert(trace.get("enforced") is False, f"{sample['id']} must be non-enforcing")
            _verify_side_effect_contract(trace)

            understanding = trace.get("understanding") or {}
            plan = trace.get("answer_plan") or {}
            _assert(understanding.get("primary_intent") == sample["expected_intent"], f"{sample['id']} intent mismatch")
            _assert(plan.get("plan_type") == sample["expected_plan"], f"{sample['id']} plan mismatch")
            results.append({
                "id": sample["id"],
                "ok": True,
                "intent": understanding.get("primary_intent"),
                "plan_type": plan.get("plan_type"),
            })

        original_context = _base_runtime_context()
        enriched = _attach_domain_shadow_trace(
            original_context,
            body="Where is my parcel now?",
            tenant_key="default",
            channel_key="website",
            market_id=None,
            language="en",
        )
        _assert(enriched is not original_context, "enabled attach must return a copied context")
        _assert("domain_intelligence_trace" in enriched, "enabled context must include domain_intelligence_trace")
        _assert("domain_intelligence_trace" not in original_context, "attach must not mutate original context")
        _assert(summarize_rag_trace(enriched) == summarize_rag_trace(original_context), "shadow trace must not change rag summary")
        _verify_side_effect_contract(enriched["domain_intelligence_trace"])
        results.append({"id": "metadata_attach_no_mutation", "ok": True})

        return {
            "ok": True,
            "probe": "domain_webchat_shadow_trace_e2e",
            "elapsed_ms": int((time.time() - started) * 1000),
            "results": results,
        }
    finally:
        if original_env is None:
            os.environ.pop("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", None)
        else:
            os.environ["DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED"] = original_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Domain Intelligence WebChat shadow trace E2E behavior.")
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON report output path.")
    args = parser.parse_args()

    report = run_probe()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
