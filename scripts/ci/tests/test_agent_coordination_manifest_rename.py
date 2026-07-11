from __future__ import annotations

import json

import pytest

import agent_coordination_path_policy as path_policy


def test_documented_question_and_character_class_globs_are_accepted() -> None:
    payload = {
        "schema": "nexus.osr.coordination.manifest.v1",
        "work_item": 521,
        "agent_run_id": "run-glob",
        "dependency": {"mode": "independent", "stack_parent_pr": None},
        "write_paths": ["backend/file?.py", "backend/[ab].py"],
        "read_paths": ["docs/guide[0-9]?.md"],
        "contracts": [],
        "database": [],
        "migrations": [],
        "generated_files": [],
        "workflows": [],
    }
    pr = {
        "body": (
            "Closes #521\n\n"
            "```json\n"
            + json.dumps(payload)
            + "\n```"
        )
    }
    manifest = path_policy.final_policy.policy.parse_manifest(pr)
    writes, reads = path_policy.final_policy.policy._access_paths(pr)

    assert manifest.write_paths == ("backend/file?.py", "backend/[ab].py")
    assert writes == ("backend/file?.py", "backend/[ab].py")
    assert reads == ("docs/guide[0-9]?.md",)
    assert path_policy._path_matches("backend/file1.py", "backend/file?.py")
    assert path_policy._path_matches("backend/a.py", "backend/[ab].py")


def test_leading_dot_segments_are_preserved() -> None:
    assert path_policy._normalize_path_spec(".env") == ".env"
    assert path_policy._normalize_path_spec(".github/workflows/x.yml") == ".github/workflows/x.yml"
    assert path_policy._normalize_path_spec("./.github/workflows/x.yml") == ".github/workflows/x.yml"
    assert path_policy._path_matches(".env", ".env")
    assert not path_policy._path_matches("env", ".env")
    assert path_policy._path_matches(
        ".github/workflows/x.yml",
        ".github/workflows/**",
    )
    assert not path_policy._path_matches(
        "github/workflows/x.yml",
        ".github/workflows/**",
    )


def test_github_reader_includes_both_sides_of_a_rename(monkeypatch) -> None:
    reader = path_policy.final_policy.policy.gate.GitHubReader(
        "Maximvonshaft/nexus_helpdesk",
        "test-token",
    )
    monkeypatch.setattr(
        reader,
        "get",
        lambda _path: {
            "number": 540,
            "state": "open",
            "draft": False,
            "body": "Closes #521",
            "head": {"sha": "a" * 40, "ref": "agent/test"},
            "base": {"ref": "main"},
            "created_at": "2026-07-11T10:00:00Z",
            "updated_at": "2026-07-11T10:05:00Z",
        },
    )
    monkeypatch.setattr(
        reader,
        "get_paginated",
        lambda _path: [
            {
                "status": "renamed",
                "filename": "backend/new_name.py",
                "previous_filename": "backend/old_name.py",
            },
            {
                "status": "modified",
                "filename": "backend/other.py",
            },
        ],
    )

    result = reader.pr(540, include_files=True)

    assert result["changed_files"] == [
        "backend/new_name.py",
        "backend/old_name.py",
        "backend/other.py",
    ]
    assert result["updated_at"] == "2026-07-11T10:05:00Z"


def test_rename_source_path_participates_in_actual_collision() -> None:
    renamed = {
        "number": 1,
        "state": "open",
        "body": "",
        "changed_files": ["backend/new_name.py", "backend/old_name.py"],
    }
    editing_old = {
        "number": 2,
        "state": "open",
        "body": "",
        "changed_files": ["backend/old_name.py"],
    }

    assert set(path_policy.final_policy.model._changed_files(renamed)) & set(
        path_policy.final_policy.model._changed_files(editing_old)
    ) == {"backend/old_name.py"}


def _hydration_snapshot() -> dict:
    target_issue = {
        "number": 10,
        "state": "open",
        "labels": ["osr-work-order"],
        "body": "## Control\n- Current PR: #1\n- Blocked by: #20\n",
        "comments": [],
    }
    blocker_issue = {
        "number": 20,
        "state": "open",
        "labels": ["security-control"],
        "body": "## Control\n- Current PR: #2\n- Blocked by: none\n",
        "comments": [],
    }
    return {
        "pull_request": {
            "number": 1,
            "state": "open",
            "body": "Closes #10",
            "changed_files": ["backend/target.py"],
        },
        "work_item": target_issue,
        "open_work_items": [target_issue, blocker_issue],
        "open_pull_requests": [
            {
                "number": 1,
                "state": "open",
                "body": "Closes #10",
                "changed_files": ["backend/target.py"],
            },
            {
                "number": 2,
                "state": "open",
                "body": "Closes #20",
                "changed_files": [],
            },
        ],
    }


def test_blocker_hydration_refetches_new_current_pr_files() -> None:
    snapshot = _hydration_snapshot()
    calls: list[tuple[int, bool]] = []

    def load_pr(number: int, *, include_files: bool):
        calls.append((number, include_files))
        return {
            "number": number,
            "state": "open",
            "body": f"Closes #{10 if number == 1 else 20}",
            "changed_files": (
                ["backend/target.py"]
                if number == 1
                else ["backend/new_name.py", "backend/old_name.py"]
            ),
        }

    hydrated = path_policy._hydrate_current_pr_files(snapshot, load_pr)
    by_number = {pr["number"]: pr for pr in hydrated["open_pull_requests"]}

    assert calls == [(1, True), (2, True)]
    assert by_number[2]["changed_files"] == [
        "backend/new_name.py",
        "backend/old_name.py",
    ]
    assert hydrated["pull_request"]["changed_files"] == ["backend/target.py"]


def test_closed_recorded_current_pr_fails_closed() -> None:
    snapshot = _hydration_snapshot()

    def load_pr(number: int, *, include_files: bool):
        assert include_files is True
        return {
            "number": number,
            "state": "closed" if number == 2 else "open",
            "body": f"Closes #{10 if number == 1 else 20}",
            "changed_files": [],
        }

    with pytest.raises(
        path_policy.final_policy.model.GateInputError,
        match="current_pr_not_open:pr:2",
    ):
        path_policy._hydrate_current_pr_files(snapshot, load_pr)


def test_blocker_current_pr_file_lookup_fails_closed() -> None:
    issue = {
        "number": 10,
        "state": "open",
        "labels": ["osr-work-order"],
        "body": "## Control\n- Current PR: #1\n- Blocked by: none\n",
        "comments": [],
    }
    snapshot = {
        "pull_request": {"number": 1, "state": "open", "body": "Closes #10", "changed_files": []},
        "work_item": issue,
        "open_work_items": [issue],
        "open_pull_requests": [
            {"number": 1, "state": "open", "body": "Closes #10", "changed_files": []}
        ],
    }

    def unavailable(_number: int, *, include_files: bool):
        assert include_files is True
        raise RuntimeError("network detail must not escape")

    with pytest.raises(
        path_policy.final_policy.model.GateInputError,
        match="current_pr_file_lookup_unavailable:pr:1",
    ):
        path_policy._hydrate_current_pr_files(snapshot, unavailable)
