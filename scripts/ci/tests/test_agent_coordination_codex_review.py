from __future__ import annotations

import json

import pytest

import agent_coordination_policy_gate as policy


def test_reclaim_without_prior_lease_fails_closed() -> None:
    leases, findings = policy.model.parse_leases(
        [
            {
                "body": "## AGENT_RECLAIM\n- New Agent Run ID: `run-new`",
                "created_at": "2026-07-11T10:00:00Z",
            }
        ]
    )

    assert not leases
    assert "reclaim_without_prior_lease" in {finding.code for finding in findings}


def test_specific_file_globs_with_real_intersection_overlap() -> None:
    assert policy.model._path_specs_overlap(
        "services/*/config.yml",
        "services/api/*.yml",
    )


def test_disjoint_fixed_prefix_and_suffix_globs_do_not_overlap() -> None:
    assert not policy.model._path_specs_overlap(
        "services/web/a*.py",
        "services/api/b*.json",
    )


def test_recursive_glob_and_specific_file_overlap() -> None:
    assert policy.model._path_specs_overlap(
        "backend/**",
        "backend/app/settings.py",
    )


def test_malformed_stack_parent_becomes_bounded_gate_error() -> None:
    body = """Closes #521

```json
{"schema":"nexus.osr.coordination.manifest.v1","work_item":521,"agent_run_id":"run-x","dependency":{"mode":"stacked","stack_parent_pr":"not-a-number"},"write_paths":["scripts/ci/**"],"contracts":[],"database":[],"migrations":[],"generated_files":[],"workflows":[]}
```
"""
    with pytest.raises(policy.model.GateInputError, match="manifest_stack_parent_invalid"):
        policy.model.parse_manifest({"body": body})


def test_manifest_with_integer_stack_parent_remains_valid() -> None:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": 521,
        "agent_run_id": "run-x",
        "dependency": {"mode": "stacked", "stack_parent_pr": 540},
        "write_paths": ["scripts/ci/**"],
        "contracts": [],
        "database": [],
        "migrations": [],
        "generated_files": [],
        "workflows": [],
    }
    manifest = policy.model.parse_manifest(
        {"body": f"Closes #521\n\n```json\n{json.dumps(payload)}\n```"}
    )
    assert manifest.stack_parent_pr == 540
