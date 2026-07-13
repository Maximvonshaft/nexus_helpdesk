from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "webchat-live-edge-credential-rotation.md"


def _read() -> str:
    if not RUNBOOK.exists():
        return ""
    return RUNBOOK.read_text(encoding="utf-8")


def test_rotation_runbook_exists() -> None:
    assert RUNBOOK.exists()


def test_runbook_keeps_execution_blocked_without_both_authorizations() -> None:
    text = _read()
    required = [
        "BLOCKED / UNVERIFIED / NO_GO",
        "explicit production access approval",
        "controlled Credential Rotation Window",
        "must not read, validate, issue, install, revoke, reload, restart, or probe",
    ]
    for marker in required:
        assert marker in text


def test_runbook_records_inventory_custody_and_exact_identity_without_values() -> None:
    text = _read()
    required = [
        "release Git SHA",
        "image tag or digest",
        "configuration fingerprint",
        "process or container identity",
        "secret reference identifier",
        "Custody Owner",
        "Execution Owner",
        "UTC Timestamp",
        "Change / Approval ID",
    ]
    for marker in required:
        assert marker in text


def test_runbook_uses_server_side_secret_file_and_prohibits_secret_surfaces() -> None:
    text = _read()
    required = [
        "LIVE_VOICE_UPSTREAM_TOKEN_FILE",
        "/run/nexus/ai_runtime_token",
        "browser: zero secret",
        "Git: zero secret",
        "artifact: zero secret",
        "log: zero secret",
        "Authorization values",
        "tokenized URLs",
        "secret value, derivative, digest, prefix, suffix, or length",
    ]
    for marker in required:
        assert marker in text


def test_runbook_defines_health_websocket_and_predecessor_invalidation_evidence() -> None:
    text = _read()
    required = [
        "/webchat/live/health",
        "/webchat/live/ws",
        "HTTP 101",
        "replacement credential works before predecessor revocation",
        "predecessor authentication is rejected",
        "Do not place either credential in a command line",
        "bounded redacted result",
    ]
    for marker in required:
        assert marker in text


def test_runbook_has_pre_and_post_revocation_rollback_and_fail_closed_path() -> None:
    text = _read()
    required = [
        "Rollback before predecessor revocation",
        "Rollback after predecessor revocation",
        "WEBCHAT_VOICE_ENABLED=false",
        "remove the live upstream URLs and token-file reference",
        "incident escalation",
    ]
    for marker in required:
        assert marker in text


def test_runbook_contains_no_credential_shaped_example() -> None:
    text = _read()
    forbidden_patterns = [
        r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}",
        r"(?i)(?:token|secret|api[_-]?key)\s*[=:]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}",
        r"(?i)[?&](?:token|access_token|api_key)=[A-Za-z0-9._~+/=-]{8,}",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, text) is None


def test_websocket_probe_does_not_stream_raw_headers_or_bodies() -> None:
    text = _read()
    assert "--dump-header -" not in text
    assert "The probe output is restricted to the HTTP status code and curl exit code." in text
