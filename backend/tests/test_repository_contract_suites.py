from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_deployment_and_remediation_contract_suites_are_canonical_backend_gates() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "backend/tests/test_customer_visible_channel_compatibility.py",
            "backend/tests/test_canonical_policy_projection_behavior.py::test_current_scope_projection_contains_only_active_current_user_grants",
            "backend/tests/test_conversation_first_agent_routing.py::test_ticketless_transition_authority_and_reply_capability",
            "backend/tests/test_webchat_ai_terminal_outcome_convergence.py::test_no_public_runtime_result_exhausts_into_one_canonical_fallback",
            "backend/tests/test_webchat_ai_terminal_outcome_convergence.py::test_watchdog_timeout_uses_same_idempotent_terminal_outcome",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_ai_turn_completes_and_clears_pending_after_dispatch",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_clarifying_question_runtime_reply_is_not_suppressed",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_ai_turn_runtime_rejects_handoff_claim_without_tool_side_effect",
            "backend/tests/test_webchat_ai_turn_runtime.py::test_reconciler_times_out_stale_bridge_calling_turn",
            "backend/tests/test_webchat_round_b.py::test_public_webchat_init_send_poll_and_background_ai_reply",
            "backend/tests/test_webchat_terminal_fallback_delivery.py::test_exhausted_webchat_ai_job_commits_one_safe_ticketless_terminal_outcome",
            "backend/tests/test_whatsapp_embedded_signup_retryability.py",
            "backend/tests/test_ticketless_whatsapp_delivery.py",
            "scripts/release/tests",
            "scripts/deploy/tests",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout
