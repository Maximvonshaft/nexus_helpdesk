#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
brief = ROOT / "06_engineering_work_plan/execution_brief.md"
criteria = ROOT / "07_quality_testing/acceptance_criteria.md"
rollback = ROOT / "11_release_change_management/rollback_plan.md"

print("# PR Body Draft")
print()
print("## Execution Brief")
print(brief.read_text(encoding="utf-8")[:3000])
print()
print("## Acceptance Criteria")
print(criteria.read_text(encoding="utf-8")[:3000])
print()
print("## Rollback")
print(rollback.read_text(encoding="utf-8")[:2000])
