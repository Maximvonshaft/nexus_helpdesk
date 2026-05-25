from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "docs" / "email_outbound_pack"

REQUIRED = [
    "README.md",
    "SSS_PRODUCTION_READINESS_SCORECARD.md",
    "02_problem_definition_evidence/main_code_reference_map_v1_4.csv",
    "03_scope_contract/p0_p1_guardrails_addendum.md",
    "04_solution_architecture/email_runtime_gate_and_account_resolver_design.md",
    "05_design_ux_content/admin_email_account_configuration_ui_spec_v1_2.md",
    "05_design_ux_content/agent_email_reply_composer_ui_spec_v1_2.md",
    "06_engineering_work_plan/pr_slicing_plan_v1_4.md",
    "06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv",
    "06_engineering_work_plan/atomic_issue_template_examples_v1_4.md",
    "06_engineering_work_plan/final_codex_execution_gate_v1_4.md",
    "07_quality_testing/task_to_test_traceability_v1_4.csv",
    "07_quality_testing/business_value_end_to_end_trace_v1_4.csv",
    "07_quality_testing/e2e_business_acceptance_checklist_v1_2.md",
    "09_devops_ci_cd/admin_configurable_vs_devops_controlled_matrix_v1_2.csv",
    "11_release_change_management/atomic_rollback_matrix_v1_4.csv",
    "11_release_change_management/rollback_plan.md",
    "12_operations_training/admin_email_configuration_sop_v1_2.md",
]

for rel in REQUIRED:
    path = ROOT / rel
    assert path.exists(), f"Missing required file: {rel}"
    assert path.stat().st_size > 50, f"File too small: {rel}"

readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "v1.4" in readme, "README must identify v1.4"
assert "atomic_delivery_execution_board_v1_4.csv" in readme, "README must point to v1.4 source of truth"

scorecard = (ROOT / "SSS_PRODUCTION_READINESS_SCORECARD.md").read_text(encoding="utf-8")
assert "95 / 100" in scorecard, "Scorecard must be updated for v1.4"
assert "Atomic Delivery Pack" in scorecard, "Scorecard must rate atomic delivery readiness"

with (ROOT / "06_engineering_work_plan/atomic_delivery_execution_board_v1_4.csv").open(encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

required_columns = {
    "Task ID",
    "Epic",
    "PR Slice",
    "Layer",
    "Owner Role",
    "Depends On",
    "Atomic Change",
    "Primary Files",
    "Exact Code-Level Requirement",
    "Acceptance Criteria",
    "Test Command",
    "Expected Evidence",
    "Rollback Impact",
    "Merge Independence",
    "Priority",
}
assert rows, "Atomic delivery board must not be empty"
assert required_columns.issubset(rows[0].keys()), f"Missing columns: {required_columns - set(rows[0].keys())}"
assert len(rows) >= 80, f"Atomic delivery board must have at least 80 tasks, found {len(rows)}"

task_ids = [row["Task ID"] for row in rows]
assert len(task_ids) == len(set(task_ids)), "Task IDs must be unique"
assert any(t.startswith("EMAIL-BE-") for t in task_ids), "Missing backend tasks"
assert any(t.startswith("EMAIL-FE-") for t in task_ids), "Missing frontend tasks"
assert any(t.startswith("EMAIL-DB-") for t in task_ids), "Missing database tasks"
assert any(t.startswith("EMAIL-OPS-") for t in task_ids), "Missing ops tasks"
assert any(t.startswith("EMAIL-QA-") for t in task_ids), "Missing QA tasks"

for row in rows:
    for col in required_columns:
        assert row[col].strip(), f"{row.get('Task ID')} missing {col}"
    assert row["Priority"] in {"P0", "P1", "P2", "P3"}, f"Invalid priority: {row['Task ID']}"
    assert row["Merge Independence"].lower() in {"yes", "no"}, f"Invalid merge independence: {row['Task ID']}"
    assert row["Test Command"].strip(), f"Missing test command: {row['Task ID']}"

p0 = [row for row in rows if row["Priority"] == "P0"]
p1 = [row for row in rows if row["Priority"] == "P1"]
p2 = [row for row in rows if row["Priority"] == "P2"]
assert len(p0) >= 20, "Expected meaningful P0 coverage"
assert len(p1) >= 20, "Expected meaningful P1 coverage"
assert len(p2) >= 5, "Expected non-blocking P2 coverage"

required_task_ids = {
    "EMAIL-BE-003",
    "EMAIL-BE-016",
    "EMAIL-BE-038",
    "EMAIL-BE-049",
    "EMAIL-BE-051",
    "EMAIL-BE-064",
    "EMAIL-FE-070",
    "EMAIL-FE-078",
    "EMAIL-OPS-088",
}
missing_required = required_task_ids - set(task_ids)
assert not missing_required, f"Missing critical atomic tasks: {sorted(missing_required)}"

with (ROOT / "07_quality_testing/task_to_test_traceability_v1_4.csv").open(encoding="utf-8", newline="") as f:
    trace_rows = list(csv.DictReader(f))
trace_ids = {row["Task ID"] for row in trace_rows}
missing_trace = set(task_ids) - trace_ids
assert not missing_trace, f"Tasks missing test traceability: {sorted(missing_trace)[:20]}"
assert len(trace_rows) >= len(rows), "Traceability rows must cover all tasks"

with (ROOT / "02_problem_definition_evidence/main_code_reference_map_v1_4.csv").open(encoding="utf-8", newline="") as f:
    ref_rows = list(csv.DictReader(f))
assert len(ref_rows) >= 12, "Current-main reference map too small"
for required_path in [
    "outbound_channel_registry.py",
    "message_dispatch.py",
    "models.py",
    "schemas.py",
    "api.ts",
    "accounts.tsx",
    "CustomerReplyPanel.tsx",
]:
    assert any(required_path in row["Current main path"] for row in ref_rows), f"Missing current-main reference for {required_path}"

with (ROOT / "07_quality_testing/business_value_end_to_end_trace_v1_4.csv").open(encoding="utf-8", newline="") as f:
    bv_rows = list(csv.DictReader(f))
required_business_steps = {
    "Configure Email account",
    "Verify readiness",
    "Expose Email to agent only when safe",
    "Send customer Email reply",
    "Dispatch through SES",
    "Capture delivery lifecycle",
    "Prevent repeated bad sends",
    "Receive customer reply",
    "Operate and observe",
    "Rollback safely",
}
actual_steps = {row["Business Step"] for row in bv_rows}
assert required_business_steps.issubset(actual_steps), f"Missing business trace steps: {sorted(required_business_steps - actual_steps)}"

with (ROOT / "11_release_change_management/atomic_rollback_matrix_v1_4.csv").open(encoding="utf-8", newline="") as f:
    rollback_rows = list(csv.DictReader(f))
assert len(rollback_rows) >= 6, "Rollback matrix must cover at least six scenarios"
assert any("pending email rows remain" in row["Expected Queue Effect"].lower() for row in rollback_rows), "Missing email-only rollback queue invariant"

smoke = (ROOT / "15_automation_scripts/smoke_email_full_e2e.sh").read_text(encoding="utf-8")
assert "json_payload()" in smoke, "Full E2E smoke must build JSON safely"
assert "--mock-webhooks" in smoke, "Full E2E smoke must support mock webhook mode"
assert "--mock-inbound" in smoke, "Full E2E smoke must support mock inbound mode"
assert "--rollback-check" in smoke, "Full E2E smoke must support rollback check"
assert "timeline_after_send.json" in smoke, "Full E2E smoke must collect timeline evidence"
assert "Template only" not in smoke, "Full E2E smoke must not be a template-only placeholder"

manifest = json.loads((ROOT / "00_meta/file_manifest.json").read_text(encoding="utf-8"))
assert manifest["package"].endswith("v1_4"), "Manifest package version mismatch"
assert manifest["file_count"] >= 130, "Manifest file count unexpectedly low"

print("Email outbound implementation pack v1.4 validation passed.")
