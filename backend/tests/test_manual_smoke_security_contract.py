from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "manual-staging-smoke.yml"
PUBLIC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "public-production-smoke.yml"
PUBLIC_SMOKE = REPO_ROOT / "scripts" / "smoke" / "public_webchat_smoke.py"


def _load_public_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("public_webchat_smoke_contract", PUBLIC_SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _step_run(workflow: Path, step_name: str) -> str:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run_index = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    content_indent = run_indent + 2
    block: list[str] = []
    for line in lines[run_index + 1 :]:
        if line and len(line) - len(line.lstrip()) <= run_indent:
            break
        if line:
            block.append(line[content_indent:])
        else:
            block.append("")
    return "\n".join(block) + "\n"


def _run_validator(
    tmp_path: Path,
    workflow: Path,
    step_name: str,
    values: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output = tmp_path / "github-output.txt"
    env = os.environ.copy()
    env.update(values)
    env["GITHUB_OUTPUT"] = str(output)
    completed = subprocess.run(
        ["bash", "-c", _step_run(workflow, step_name)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    parsed: dict[str, str] = {}
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            parsed[key] = value
    return completed, parsed


def _manual_values(**overrides: str) -> dict[str, str]:
    values = {
        "RAW_BASE_URL": "https://support.example.test/",
        "RAW_EXPECTED_STATUS": "ready",
        "RAW_CORS_ORIGIN": "https://portal.example.test",
        "RAW_ACCOUNT_ID": "",
        "RAW_TEST_SEND_CONFIRM": "",
        "CHECK_TEST_SEND": "false",
    }
    values.update(overrides)
    return values


def _public_values(**overrides: str) -> dict[str, str]:
    values = {
        "RAW_BASE_URL": "https://www.example.test/",
        "RAW_EXPECTED_GIT_SHA": "",
        "RAW_EXPECTED_IMAGE_TAG": "",
        "RAW_ORIGIN": "https://www.example.test",
        "RAW_MAX_LATENCY_MS": "25000",
        "REQUIRE_AI_REPLY_INPUT": "true",
        "SKIP_AI_REPLY_INPUT": "false",
    }
    values.update(overrides)
    return values


def test_workflows_use_immutable_actions_and_least_privilege() -> None:
    for workflow in (MANUAL_WORKFLOW, PUBLIC_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        assert "permissions:\n  contents: read\n" in text
        uses_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("uses:")
        ]
        assert uses_lines
        assert all(
            re.fullmatch(r"uses: [^@]+@[0-9a-f]{40}(?: # .+)?", line)
            for line in uses_lines
        )
    assert "persist-credentials: false" in MANUAL_WORKFLOW.read_text(encoding="utf-8")
    assert "persist-credentials: false" in PUBLIC_WORKFLOW.read_text(encoding="utf-8")


def test_dispatch_strings_are_not_pasted_into_shell_or_python_source() -> None:
    manual = MANUAL_WORKFLOW.read_text(encoding="utf-8")
    public = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
    assert 'base="${{ inputs.base_url }}"' not in manual
    assert "expected = '${{ inputs.expected_status }}'" not in manual
    assert "origin='${{ inputs.cors_origin }}'" not in manual
    assert '"- Target: `${{ inputs.base_url }}`"' not in public
    assert '"- Expected git SHA: `${{ inputs.expected_git_sha }}`"' not in public
    assert "RAW_BASE_URL: ${{ inputs.base_url }}" in manual
    assert "RAW_BASE_URL: ${{ inputs.base_url }}" in public


def test_manual_validator_normalizes_safe_values(tmp_path: Path) -> None:
    completed, outputs = _run_validator(
        tmp_path,
        MANUAL_WORKFLOW,
        "Validate smoke inputs without secrets",
        _manual_values(RAW_ACCOUNT_ID="17"),
    )
    assert completed.returncode == 0, completed.stderr
    assert outputs == {
        "base_url": "https://support.example.test",
        "expected_status": "ready",
        "cors_origin": "https://portal.example.test",
        "account_id": "17",
        "test_send_authorized": "false",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"RAW_BASE_URL": "file:///etc/passwd"},
        {"RAW_BASE_URL": "https://user:pass@example.test"},
        {"RAW_BASE_URL": "https://example.test/path"},
        {"RAW_EXPECTED_STATUS": "ready'; echo pwned"},
        {"RAW_CORS_ORIGIN": "https://example.test\nINJECTED=true"},
        {"RAW_ACCOUNT_ID": "-1"},
        {"RAW_ACCOUNT_ID": "1\nINJECTED=true"},
    ],
)
def test_manual_validator_rejects_unsafe_inputs(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    completed, outputs = _run_validator(
        tmp_path,
        MANUAL_WORKFLOW,
        "Validate smoke inputs without secrets",
        _manual_values(**overrides),
    )
    assert completed.returncode != 0
    assert outputs == {}


def test_manual_real_email_requires_operator_confirmation(tmp_path: Path) -> None:
    completed, _ = _run_validator(
        tmp_path,
        MANUAL_WORKFLOW,
        "Validate smoke inputs without secrets",
        _manual_values(CHECK_TEST_SEND="true"),
    )
    assert completed.returncode != 0

    completed, outputs = _run_validator(
        tmp_path,
        MANUAL_WORKFLOW,
        "Validate smoke inputs without secrets",
        _manual_values(
            CHECK_TEST_SEND="true",
            RAW_TEST_SEND_CONFIRM="I_UNDERSTAND_THIS_SENDS_REAL_EMAIL",
        ),
    )
    assert completed.returncode == 0, completed.stderr
    assert outputs["test_send_authorized"] == "true"


def test_public_validator_normalizes_safe_values(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    completed, outputs = _run_validator(
        tmp_path,
        PUBLIC_WORKFLOW,
        "Validate public smoke inputs",
        _public_values(
            RAW_EXPECTED_GIT_SHA=sha,
            RAW_EXPECTED_IMAGE_TAG="ghcr.io/example/nexus:candidate",
        ),
    )
    assert completed.returncode == 0, completed.stderr
    assert outputs["base_url"] == "https://www.example.test"
    assert outputs["origin"] == "https://www.example.test"
    assert outputs["expected_git_sha"] == sha
    assert outputs["max_latency_ms"] == "25000"
    assert outputs["require_ai_reply"] == "true"
    assert outputs["skip_ai_reply"] == "false"


@pytest.mark.parametrize(
    "overrides",
    [
        {"RAW_BASE_URL": "ftp://example.test"},
        {"RAW_ORIGIN": "https://example.test/path"},
        {"RAW_EXPECTED_GIT_SHA": "not-a-sha"},
        {"RAW_EXPECTED_IMAGE_TAG": "tag with spaces"},
        {"RAW_MAX_LATENCY_MS": "0"},
        {"RAW_MAX_LATENCY_MS": "999999"},
        {"REQUIRE_AI_REPLY_INPUT": "true", "SKIP_AI_REPLY_INPUT": "true"},
    ],
)
def test_public_validator_rejects_unsafe_or_contradictory_inputs(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    completed, outputs = _run_validator(
        tmp_path,
        PUBLIC_WORKFLOW,
        "Validate public smoke inputs",
        _public_values(**overrides),
    )
    assert completed.returncode != 0
    assert outputs == {}


@pytest.mark.parametrize(
    ("reply_source", "required", "expected"),
    [
        ("", True, "webchat_reply_source_missing"),
        ("safe_fallback", True, "webchat_reply_source=safe_fallback"),
        ("private_ai_runtime", True, None),
        ("private_ai_runtime:qwen", True, None),
        ("", False, None),
        ("safe_fallback", False, None),
    ],
)
def test_reply_source_truth_contract(
    reply_source: str,
    required: bool,
    expected: str | None,
) -> None:
    smoke = _load_public_smoke()
    assert smoke.reply_source_error(reply_source, require_ai_reply=required) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Example.TEST/", "https://example.test"),
        ("http://127.0.0.1:18082", "http://127.0.0.1:18082"),
    ],
)
def test_public_smoke_normalizes_http_endpoints(value: str, expected: str) -> None:
    smoke = _load_public_smoke()
    assert smoke.normalize_http_endpoint(value, name="target") == expected


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "https://user:pass@example.test",
        "https://example.test/path",
        "https://example.test?query=1",
        "https://example.test\nINJECTED=true",
    ],
)
def test_public_smoke_rejects_unsafe_endpoints(value: str) -> None:
    smoke = _load_public_smoke()
    with pytest.raises(SystemExit):
        smoke.normalize_http_endpoint(value, name="target")


def test_workflow_wires_require_ai_reply_and_pending_conflict() -> None:
    public = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
    manual = MANUAL_WORKFLOW.read_text(encoding="utf-8")
    smoke = PUBLIC_SMOKE.read_text(encoding="utf-8")
    assert "args+=(--require-ai-reply)" in public
    assert "REQUIRE_AI_REPLY: ${{ steps.target.outputs.require_ai_reply }}" in public
    assert "require_ai_reply_conflicts_with_skip_ai_reply" in public
    assert "pending_ok = args.allow_pending and not args.require_ai_reply" in smoke
    assert "outbound_email_test_send_confirm" in manual
    assert "test_send_authorized == 'true'" in manual
