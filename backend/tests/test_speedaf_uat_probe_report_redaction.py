from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


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


def test_full_uat_probe_does_not_run_writes_when_required_samples_are_missing(monkeypatch, tmp_path):
    module = _load_script("speedaf_full_uat_probe.py")
    output = tmp_path / "full-uat-report.json"
    service_created = False

    class FakeAdapter:
        def query_waybills_by_caller(self, *, caller_id, country_code):
            return SimpleNamespace(ok=True, failure_reason=None, candidates=[])

    class FailingActionService:
        def __init__(self):
            nonlocal service_created
            service_created = True
            raise AssertionError("write service must not be created without required write samples")

    monkeypatch.setattr(
        module,
        "load_speedaf_mcp_config",
        lambda: SimpleNamespace(
            configured=True,
            enabled=True,
            base_url="https://uat-api.speedaf.com",
            app_code="PK000023",
            secret_key="EXbSJrzZ",
            content_type="text/plain",
            data_mode="string",
            require_sign=False,
        ),
    )
    monkeypatch.setattr(module, "SpeedafCoreAdapter", FakeAdapter)
    monkeypatch.setattr(module, "SpeedafActionService", FailingActionService)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "speedaf_full_uat_probe.py",
            "--caller-id",
            "41000000000",
            "--whatsapp-phone",
            "+41000009999",
            "--write-ack",
            module.WRITE_ACK,
            "--output-json",
            str(output),
        ],
    )

    code = module.main()

    assert code == 1
    assert service_created is False
    text = output.read_text(encoding="utf-8")
    assert "41000000000" not in text
    assert "+41000009999" not in text
    payload = json.loads(text)
    assert payload["input_guards"]["missing_write_inputs"] == ["waybill_code"]
    for name in ("work_order_create", "address_update", "cancel_order", "voice_callback"):
        assert payload["checks"][name]["skipped"] is True
        assert payload["checks"][name]["failure_reason"] == "missing_required_write_input"
