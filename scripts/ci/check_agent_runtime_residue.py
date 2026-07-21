#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RETIRED_PATHS = (
    ROOT / "backend/app/services/domain_intelligence",
    ROOT / "backend/app/domain_packs",
    ROOT / "backend/app/services/knowledge_grounding_service.py",
    ROOT / "backend/app/services/knowledge_prompt_service.py",
    ROOT / "backend/app/services/webchat_runtime_output_parser.py",
    ROOT / "backend/app/services/webchat_ai_decision_runtime/service.py",
    ROOT / "backend/app/services/provider_runtime/webchat_runtime_dispatcher.py",
    ROOT / "backend/scripts/run_domain_runtime_eval.py",
    ROOT / "backend/tests/test_runtime_context_guard.py",
    ROOT / "backend/tests/test_webchat_osr_audit_integration.py",
    ROOT / "backend/tests/test_ticketless_voice_ticket_binding.py",
    ROOT / "scripts/probe_domain_webchat_shadow_trace_e2e.py",
    ROOT / "scripts/probe_domain_webchat_shadow_trace_e2e.sh",
    ROOT / "docs/architecture/DOMAIN_INTELLIGENCE_RUNTIME.md",
    ROOT / "docs/ops/DOMAIN_RUNTIME_ROLLOUT_RUNBOOK.md",
    ROOT / "docs/ops/DOMAIN_RUNTIME_ROLLBACK_RUNBOOK.md",
)
RUNTIME_PATHS = (
    ROOT / "backend/app/services/agent_runtime",
    ROOT / "backend/app/services/ai_runtime",
    ROOT / "backend/app/services/provider_runtime",
    ROOT / "backend/app/services/webchat_ai_decision_runtime",
    ROOT / "backend/app/services/webchat_ai_service.py",
    ROOT / "backend/app/services/conversation_ai_service.py",
    ROOT / "backend/app/services/webchat_runtime_ai_service.py",
)
FORBIDDEN_RUNTIME_CONTENT = (
    "shipment_status_without_evidence",
    "_contains_live_shipment_conclusion",
    "_maybe_lookup_tracking_fact",
    "_HISTORY_TRACKING_CONTEXT_MARKERS",
    "_UNVERIFIED_SHIPMENT_OUTCOME_PATTERNS",
    "tracking_fact_summary",
    "locked_fact_grounding_conflict",
    "tracking_status_without_trusted_fact",
    "nexus.webchat_runtime_reply",
    "webchat_runtime_reply=True",
    "webchat_runtime_reply: bool",
    "_WEBCHAT_RUNTIME_SCENARIO",
    "DomainRegistry",
    "DomainPack",
    "build_webchat_domain_shadow_trace",
    "select_approved_direct_answer_override",
    "select_trusted_direct_answer_evidence",
    "support_knowledge_retrieve",
    "speedaf_lookup",
    "speedaf_query_waybills",
    "speedaf_create_work_order",
    "speedaf_cancel_order",
    "speedaf_update_address",
    "_permissions_for_tools",
    '"assistant_name": "Speedy"',
    '"brand": "Speedaf"',
)
ARCHITECTURE_PATHS = (
    ROOT / "docs/architecture/conversation-first-agent-routing.md",
)
FORBIDDEN_ARCHITECTURE_CONTENT = (
    "lazily creates or reuses the ticket required by the ticket-backed voice authority",
    "lazy ticket creation only when the existing voice workflow is initiated",
    "Initiating voice creates or reuses the necessary ticket",
)


def _python_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def main() -> int:
    failures: list[str] = []
    for path in RETIRED_PATHS:
        if path.exists():
            failures.append(
                f"retired path still exists: {path.relative_to(ROOT)}"
            )
    for root in RUNTIME_PATHS:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_RUNTIME_CONTENT:
                if marker in text:
                    failures.append(
                        f"{path.relative_to(ROOT)}: contains retired marker {marker}"
                    )
    for path in ARCHITECTURE_PATHS:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_ARCHITECTURE_CONTENT:
            if marker in text:
                failures.append(
                    f"{path.relative_to(ROOT)}: contains retired architecture marker {marker}"
                )
    if failures:
        print("\n".join(failures))
        return 1
    print("Agent Runtime residue check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
