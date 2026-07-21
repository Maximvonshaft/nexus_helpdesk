from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ALLOWLIST = ROOT / "config/security/secret-scan-allowlist.json"
EXPECTED_ENTRIES = (
    (
        {
            "path": "backend/tests/test_agent_runtime_architecture.py",
            "rule": "bearer_token",
            "fingerprint": "16ebd1f81952894c",
            "reason": (
                "Historical synthetic bearer fixture used to prove agent "
                "output-contract credential blocking."
            ),
            "expires_on": "2026-10-31",
        },
        "730db233de26139b310a0c9eec2c4f55da59e4b53b42102889e44e9bf2f1a0ab",
    ),
    (
        {
            "path": "backend/tests/test_nexus_osr_audit_persistence_safety.py",
            "rule": "openai_key",
            "fingerprint": "01487e7858b927eb",
            "reason": (
                "Historical synthetic OpenAI-key-shaped fixture used to prove "
                "final audit persistence sanitization."
            ),
            "expires_on": "2026-10-31",
        },
        "b52649a73019b6b8f8f35fc9fbf8ede7f205d86cbac508facb3ba2226fc90179",
    ),
)


def test_agent_runtime_history_fixture_allowlist_is_exact_and_expiring() -> None:
    payload = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "nexus_secret_scan_allowlist_v1"

    for expected, expected_path_sha256 in EXPECTED_ENTRIES:
        matches = [
            entry
            for entry in payload["entries"]
            if entry.get("path") == expected["path"]
            and entry.get("rule") == expected["rule"]
            and entry.get("fingerprint") == expected["fingerprint"]
        ]

        assert matches == [expected]
        assert (
            hashlib.sha256(expected["path"].encode("utf-8")).hexdigest()
            == expected_path_sha256
        )
        assert date.fromisoformat(expected["expires_on"]) > date.today()
