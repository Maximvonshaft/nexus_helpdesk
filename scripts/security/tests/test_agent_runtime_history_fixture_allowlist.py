from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ALLOWLIST = ROOT / "config/security/secret-scan-allowlist.json"
EXPECTED_ENTRY = {
    "path": "backend/tests/test_agent_runtime_architecture.py",
    "rule": "bearer_token",
    "fingerprint": "16ebd1f81952894c",
    "reason": (
        "Historical synthetic bearer fixture used to prove agent "
        "output-contract credential blocking."
    ),
    "expires_on": "2026-10-31",
}
EXPECTED_PATH_SHA256 = (
    "730db233de26139b310a0c9eec2c4f55da59e4b53b42102889e44e9bf2f1a0ab"
)


def test_agent_runtime_history_fixture_allowlist_is_exact_and_expiring() -> None:
    payload = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "nexus_secret_scan_allowlist_v1"

    matches = [
        entry
        for entry in payload["entries"]
        if entry.get("path") == EXPECTED_ENTRY["path"]
        and entry.get("rule") == EXPECTED_ENTRY["rule"]
        and entry.get("fingerprint") == EXPECTED_ENTRY["fingerprint"]
    ]

    assert matches == [EXPECTED_ENTRY]
    assert (
        hashlib.sha256(EXPECTED_ENTRY["path"].encode("utf-8")).hexdigest()
        == EXPECTED_PATH_SHA256
    )
    assert date.fromisoformat(EXPECTED_ENTRY["expires_on"]) > date.today()
