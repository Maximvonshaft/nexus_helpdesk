from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_BATCH_TIMEOUT_SECONDS = 600

CONTRACT_BATCHES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "routing-policy-and-osr",
        (
            "backend/tests/test_customer_visible_channel_compatibility.py",
            "backend/tests/test_canonical_policy_projection_behavior.py",
            "backend/tests/test_unified_operator_queue.py",
            "backend/tests/test_conversation_first_agent_routing.py",
            "backend/tests/test_agent_routing_scenario_live_integration.py",
            "backend/tests/test_channel_workbench_backend_contracts.py::test_formal_webcall_uses_scope_offer_handoff_and_durable_commands",
            "backend/tests/test_channel_workbench_backend_contracts.py::test_customer_hangup_closes_formal_webcall_without_duplicate_owner",
            "backend/tests/test_nexus_osr_case_context_identity.py",
            "backend/tests/test_nexus_osr_tool_execution_service.py",
        ),
    ),
    (
        "webchat-runtime",
        (
            "backend/tests/test_webchat_ai_terminal_outcome_convergence.py::test_no_public_runtime_result_exhausts_into_one_canonical_fallback",
            "backend/tests/test_webchat_ai_terminal_outcome_convergence.py::test_watchdog_timeout_uses_same_idempotent_terminal_outcome",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_ai_turn_completes_and_clears_pending_after_dispatch",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_clarifying_question_runtime_reply_is_not_suppressed",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_ai_turn_runtime_rejects_handoff_claim_without_tool_side_effect",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_reconciler_times_out_stale_bridge_calling_turn",
            "backend/tests/test_webchat_round_b.py::test_public_webchat_init_send_poll_and_background_ai_reply",
            "backend/tests/test_webchat_terminal_fallback_delivery.py::test_exhausted_webchat_ai_job_commits_one_safe_ticketless_terminal_outcome",
        ),
    ),
    (
        "whatsapp-lifecycle",
        (
            "backend/tests/test_whatsapp_embedded_signup_retryability.py",
            "backend/tests/test_whatsapp_embedded_signup_concurrency.py::test_losing_completion_request_does_not_overwrite_active_claim",
            "backend/tests/test_ticketless_whatsapp_delivery.py",
            "backend/tests/test_whatsapp_ticketless_media_processing_scope.py",
            "backend/tests/test_whatsapp_ticketless_media_projection.py",
            "backend/tests/test_whatsapp_privacy_lifecycle.py",
            "backend/tests/test_whatsapp_ticket_account_affinity.py",
            "backend/tests/test_whatsapp_shared_waba_webhook.py",
            "backend/tests/test_whatsapp_shared_waba_route_precedence.py",
        ),
    ),
    (
        "release-and-deploy",
        (
            "scripts/release/tests",
            "scripts/deploy/tests",
        ),
    ),
)


def _decode_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return "<no subprocess output captured>"
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


@pytest.mark.parametrize(
    ("batch_name", "targets"),
    CONTRACT_BATCHES,
    ids=tuple(batch_name for batch_name, _targets in CONTRACT_BATCHES),
)
def test_release_deployment_and_remediation_contract_suites_are_canonical_backend_gates(
    batch_name: str,
    targets: tuple[str, ...],
) -> None:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-vv",
        "--tb=short",
        *targets,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=CONTRACT_BATCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"canonical backend contract batch {batch_name!r} exceeded "
            f"{CONTRACT_BATCH_TIMEOUT_SECONDS}s\n"
            f"{_decode_timeout_output(exc.stdout)}",
            pytrace=False,
        )

    assert completed.returncode == 0, (
        f"canonical backend contract batch {batch_name!r} failed\n"
        f"{completed.stdout}"
    )
