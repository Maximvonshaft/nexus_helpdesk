from __future__ import annotations

from scripts.ci.provider_runtime_hygiene import (
    OPENAI_RESPONSE_PROBE_PATH,
    POLICY_SOURCE_PATHS,
    SCHEMA,
    scan_text,
    scannable_paths,
)


def _codes(path: str, text: str) -> set[str]:
    return {str(row["code"]) for row in scan_text(path, text)}


def test_exact_openai_response_api_probe_declarations_are_allowed() -> None:
    text = """
ACTIVE_TEST_NAMES = {
    "openai_responses",
}
TEST_OPENAI_RESPONSES = "openai_responses"

def _test_openai_responses_api(
    context,
):
    return context

TEST_FUNCTIONS = {
    TEST_OPENAI_RESPONSES: _test_openai_responses_api,
}

def _active_specs(paths):
    tests = []
    if paths.get("/v1/responses") not in {None, "not_found"}:
        tests.append("openai_responses")
    return tests

def _active_request(target, test_name, active, endpoints):
    if False:
        return None
    elif test_name == "openai_responses":
        endpoint = target.endpoints.get("openai_responses") or _join_target_url(target.base_url, "/v1/responses")
    elif _active_pass(active.get("openai_responses")) or _present(endpoints.get("/v1/responses")):
        return {"fit": "adapter_required"}
    return {
        "openai_responses": verdict("/v1/responses", "openai_responses"),
    }
"""
    assert scan_text(OPENAI_RESPONSE_PROBE_PATH, text) == []


def test_openai_responses_provider_authority_remains_forbidden() -> None:
    assert _codes(
        "backend/app/settings.py",
        'PROVIDER_RUNTIME_PRIMARY_PROVIDER = "openai_responses"\n',
    ) == {"retired_openai_responses_provider_identifier"}
    assert _codes(
        OPENAI_RESPONSE_PROBE_PATH,
        'PROVIDER_ADAPTERS["openai_responses"] = OpenAIResponsesAdapter\n',
    ) == {"retired_openai_responses_provider_identifier"}


def test_same_probe_key_is_not_allowed_in_other_executable_paths() -> None:
    assert _codes(
        "scripts/ai/parallel_probe.py",
        'TEST_OPENAI_RESPONSES = "openai_responses"\n',
    ) == {"retired_openai_responses_provider_identifier"}


def test_only_policy_source_and_test_corpus_are_excluded() -> None:
    candidates = [
        *POLICY_SOURCE_PATHS,
        "scripts/ci/provider_runtime_hygiene_shadow.py",
        "scripts/ai/probe_ai_resource_server.py",
        "backend/app/settings.py",
    ]
    assert scannable_paths(candidates) == [
        "backend/app/settings.py",
        "scripts/ai/probe_ai_resource_server.py",
        "scripts/ci/provider_runtime_hygiene_shadow.py",
    ]


def test_other_retired_provider_and_canned_reply_markers_fail_closed() -> None:
    text = "\n".join(
        (
            "provider = 'codex_app_server'",
            "fallback = 'codex_direct'",
            "mode = 'webchat_fast_reply'",
            "message = 'Please provide your tracking number'",
        )
    )
    assert _codes("backend/app/example.py", text) == {
        "retired_codex_app_server",
        "retired_codex_direct",
        "retired_webchat_fast_reply",
        "retired_canned_tracking_reply",
    }


def test_findings_are_bounded_metadata_without_source_text() -> None:
    findings = scan_text("backend/app/example.py", "provider = 'codex_direct'\n")
    assert findings == [
        {"code": "retired_codex_direct", "path": "backend/app/example.py", "line": 1}
    ]
    assert SCHEMA == "nexus.provider-runtime.hygiene.v1"
