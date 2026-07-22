#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RETIRED_PATHS = (
    ROOT / "backend/app/services/agent_runtime/fallback.py",
    ROOT / "backend/app/services/domain_intelligence",
    ROOT / "backend/app/domain_packs",
    ROOT / "backend/app/services/knowledge_grounding_service.py",
    ROOT / "backend/app/services/knowledge_prompt_service.py",
    ROOT / "backend/app/services/webchat_runtime_output_parser.py",
    ROOT / "backend/app/services/webchat_ai_decision_runtime/service.py",
    ROOT / "backend/app/services/provider_runtime/webchat_runtime_dispatcher.py",
    ROOT / "backend/app/services/webchat_osr_audit_service.py",
    ROOT / "backend/app/services/nexus_osr/runtime_bridge.py",
    ROOT / "backend/app/services/nexus_osr/tool_execution_facade.py",
    ROOT / "backend/app/services/nexus_osr/escalation_orchestration_service.py",
    ROOT / "backend/app/services/conversation_ai_service.py",
    ROOT / "backend/app/services/llm_service.py",
    ROOT / "backend/app/services/auto_reply_service.py",
    ROOT / "backend/scripts/run_api_manual.py",
    ROOT / "backend/scripts/run_worker_manual.py",
    ROOT / "deploy/systemd/nexusdesk-worker.service",
    ROOT / "backend/evals/nexus_osr",
    ROOT / "backend/scripts/run_domain_runtime_eval.py",
    ROOT / "backend/scripts/run_nexus_osr_eval.py",
    ROOT / "backend/tests/test_runtime_context_guard.py",
    ROOT / "backend/tests/test_webchat_osr_audit_integration.py",
    ROOT / "backend/tests/test_ticketless_voice_ticket_binding.py",
    ROOT / "backend/tests/test_nexus_osr_runtime_bridge.py",
    ROOT / "backend/tests/test_nexus_osr_tool_execution_facade.py",
    ROOT / "backend/tests/test_nexus_osr_release_integration.py",
    ROOT / "backend/tests/test_nexus_osr_escalation_orchestration.py",
    ROOT / "backend/tests/test_nexus_osr_queue_escalation_entrypoint.py",
    ROOT / "backend/tests/test_nexus_osr_eval_schema.py",
    ROOT / "backend/tests/test_nexus_osr_eval_runner.py",
    ROOT / "scripts/probe_domain_webchat_shadow_trace_e2e.py",
    ROOT / "scripts/probe_domain_webchat_shadow_trace_e2e.sh",
    ROOT / "docs/architecture/DOMAIN_INTELLIGENCE_RUNTIME.md",
    ROOT / "docs/architecture/osr-agent-workstreams/agent-1-webchat-osr-audit.md",
    ROOT / "docs/architecture/osr-agent-workstreams/agent-3-tool-execution-auto-ticket.md",
    ROOT / "docs/ops/DOMAIN_RUNTIME_ROLLOUT_RUNBOOK.md",
    ROOT / "docs/ops/DOMAIN_RUNTIME_ROLLBACK_RUNBOOK.md",
    ROOT / "docs/ops/NEXUS_OSR_EVAL_RUNBOOK.md",
)

# Retired Agent markers are contextual. Scan only modules that own Agent
# generation/orchestration, not unrelated business integrations whose canonical
# Tool names may contain the same words.
AGENT_MARKER_PATHS = (
    ROOT / "backend/app/services/agent_runtime",
    ROOT / "backend/app/services/ai_runtime",
    ROOT / "backend/app/services/provider_runtime",
    ROOT / "backend/app/services/webchat_ai_decision_runtime",
    ROOT / "backend/app/services/webchat_ai_service.py",
    ROOT / "backend/app/services/webchat_runtime_ai_service.py",
    ROOT / "backend/app/services/webchat_ai_orchestration_service.py",
    ROOT / "backend/app/services/background_jobs.py",
    ROOT / "backend/app/services/background_job_transaction_boundary.py",
    ROOT / "backend/app/services/lite_service.py",
    ROOT / "backend/app/api/lite.py",
)
DIRECT_MODEL_SCAN_PATHS = (
    ROOT / "backend/app/services",
    ROOT / "backend/app/api",
    ROOT / "backend/scripts",
)
TOOL_GOVERNANCE_PATHS = (ROOT / "backend/app/services/nexus_osr",)
TEST_GOVERNANCE_PATHS = (ROOT / "backend/tests",)
ARCHITECTURE_PATHS = (
    ROOT / "docs/architecture/conversation-first-agent-routing.md",
)

FORBIDDEN_RUNTIME_CONTENT = (
    "**_legacy",
    "customer_visible_runtime_fallback",
    "from .agent_runtime.fallback",
    "from .fallback import customer_visible_runtime_fallback",
    "def _fallback(language:",
    "def _localized_fallback(",
    "arguments=dict(action.arguments),\n                idempotency_key=None",
    "tracking_missing_number",
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
    "handoff_required=decision.handoff_required",
    "AUTO_REPLY_JOB",
    "enqueue_auto_reply_job",
    "fire_and_forget_auto_reply",
    "process_ticketless_ai_reply",
    "from .conversation_ai_service",
    "from ..services.conversation_ai_service",
    "def list_lite_cases(",
    "legacy: bool = Query",
)

FORBIDDEN_TOOL_GOVERNANCE_CONTENT = (
    "ai_decision: AIDecision | None = None",
    "ai_decision or _decision_for_policy_gate",
    "OSRToolExecutionFacade",
    "OSRToolExecutionMode",
    "default_handlers",
    "ticket_create_handler",
    "handoff_request_handler",
    "evaluate_runtime_decision",
    "EvidenceType",
    "EvidenceSource",
    "TRACKING_STATUS_ANSWER",
    "KNOWLEDGE_ANSWER",
    "COMPLAINT_ESCALATION",
    "COMPENSATION_ESCALATION",
    "tracking_status_without_mcp_current_status",
    "knowledge_answer_without_customer_visible_knowledge",
)

FORBIDDEN_TEST_GOVERNANCE_CONTENT = (
    "/tmp/nexus-backend/source-export",
    "bounded source export completed",
)

FORBIDDEN_ARCHITECTURE_CONTENT = (
    "lazily creates or reuses the ticket required by the ticket-backed voice authority",
    "lazy ticket creation only when the existing voice workflow is initiated",
    "Initiating voice creates or reuses the necessary ticket",
    "through revision `20260720_0067`",
    "conversation_ai_service.py",
)

DIRECT_MODEL_CLI = re.compile(
    r"subprocess\.(?:run|Popen|check_output)\s*\([^\n]*(?:gemini|ollama|llama|openai)",
    flags=re.IGNORECASE,
)


def _python_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from path.rglob("*.py")


def _scan_markers(
    *,
    roots: tuple[Path, ...],
    markers: tuple[str, ...],
    failures: list[str],
) -> None:
    for root in roots:
        for path in _python_files(root):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                if marker in text:
                    failures.append(
                        f"{path.relative_to(ROOT)}: contains retired marker {marker}"
                    )


def _scan_direct_model_cli(failures: list[str]) -> None:
    for root in DIRECT_MODEL_SCAN_PATHS:
        for path in _python_files(root):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if DIRECT_MODEL_CLI.search(text):
                failures.append(
                    f"{path.relative_to(ROOT)}: invokes a model CLI outside Provider Runtime"
                )


def _facade_failures() -> list[str]:
    path = ROOT / "backend/app/services/webchat_service.py"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    forbidden = (
        "def create_or_resume_conversation(",
        "Ticket(",
        "Customer(",
        "create_customer_visible_message(",
        "evaluate_customer_visible_policy(",
        "generate_webchat_runtime_reply(",
    )
    return [
        f"{path.relative_to(ROOT)}: stable facade contains business marker {marker}"
        for marker in forbidden
        if marker in text
    ]


def main() -> int:
    failures: list[str] = []
    for path in RETIRED_PATHS:
        if path.exists():
            failures.append(
                f"retired path still exists: {path.relative_to(ROOT)}"
            )
    _scan_markers(
        roots=AGENT_MARKER_PATHS,
        markers=FORBIDDEN_RUNTIME_CONTENT,
        failures=failures,
    )
    _scan_markers(
        roots=TOOL_GOVERNANCE_PATHS,
        markers=FORBIDDEN_TOOL_GOVERNANCE_CONTENT,
        failures=failures,
    )
    _scan_markers(
        roots=TEST_GOVERNANCE_PATHS,
        markers=FORBIDDEN_TEST_GOVERNANCE_CONTENT,
        failures=failures,
    )
    _scan_direct_model_cli(failures)
    failures.extend(_facade_failures())
    for path in ARCHITECTURE_PATHS:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_ARCHITECTURE_CONTENT:
            if marker in text:
                failures.append(
                    f"{path.relative_to(ROOT)}: contains retired architecture marker {marker}"
                )
    if failures:
        print("\n".join(sorted(set(failures))))
        return 1
    print("Agent Runtime residue check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
