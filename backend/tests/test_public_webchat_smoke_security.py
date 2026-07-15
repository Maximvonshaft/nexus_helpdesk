from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SMOKE = REPO_ROOT / "scripts" / "smoke" / "public_webchat_smoke.py"


def _load_public_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("public_webchat_smoke_contract", PUBLIC_SMOKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
def test_reply_source_truth_contract(reply_source: str, required: bool, expected: str | None) -> None:
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


def test_public_evidence_is_recursively_redacted(tmp_path: Path) -> None:
    smoke = _load_public_smoke()
    payload = {
        "visitor_token": "visitor-secret",
        "message": {
            "body": "tracking ME020000000001",
            "metadata_json": {"reply_source": "private_ai_runtime"},
        },
        "nested": [
            {
                "authorization": "Bearer secret",
                "email": "customer@example.test",
                "safe_id": 17,
            }
        ],
    }
    safe = smoke.redact_sensitive(payload)
    assert safe["visitor_token"] == smoke.REDACTED
    assert safe["message"]["body"] == smoke.REDACTED
    assert safe["message"]["metadata_json"] == smoke.REDACTED
    assert safe["nested"][0]["authorization"] == smoke.REDACTED
    assert safe["nested"][0]["email"] == smoke.REDACTED
    assert safe["nested"][0]["safe_id"] == 17

    smoke.write_json(tmp_path, "evidence.json", payload)
    raw = (tmp_path / "evidence.json").read_text(encoding="utf-8")
    assert "visitor-secret" not in raw
    assert "tracking ME020000000001" not in raw
    assert "customer@example.test" not in raw
    assert json.loads(raw)["nested"][0]["safe_id"] == 17
