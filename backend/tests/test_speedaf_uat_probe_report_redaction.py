from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "backend" / "scripts" / name
    backend_path = str(ROOT / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_uat_report_renderer_redacts_exact_sensitive_values():
    module = _load_script("speedaf_full_uat_probe.py")
    report = {
        "ok": False,
        "checks": {
            "leaky_error": {
                "message": "caller 41000000000 waybill CH020000006856 phone +41000009999 app PK000023",
            }
        },
    }

    text = module.render_redacted_report(
        report,
        sensitive_values=["41000000000", "CH020000006856", "+41000009999", "PK000023"],
    )

    assert "41000000000" not in text
    assert "CH020000006856" not in text
    assert "+41000009999" not in text
    assert "PK000023" not in text
    payload = json.loads(text)
    assert payload["checks"]["leaky_error"]["message"].count("[REDACTED]") == 4
    assert payload["redaction_guard"]["replacement_count"] == 4


def test_readonly_uat_report_renderer_redacts_exact_sensitive_values():
    module = _load_script("speedaf_readonly_uat_probe.py")
    report = {
        "ok": False,
        "checks": {
            "configuration": {
                "message": "bad secret EXbSJrzZ for caller 41000000000 and waybill CH020000006856",
            }
        },
    }

    text = module.render_redacted_report(
        report,
        sensitive_values=module.sensitive_values("EXbSJrzZ", "41000000000", "CH020000006856"),
    )

    assert "EXbSJrzZ" not in text
    assert "41000000000" not in text
    assert "CH020000006856" not in text
    payload = json.loads(text)
    assert payload["checks"]["configuration"]["message"].count("[REDACTED]") == 3
    assert payload["redaction_guard"]["replacement_count"] == 3
