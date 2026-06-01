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


def test_full_uat_probe_runs_all_surfaces_with_ack_and_redacts_report(monkeypatch, tmp_path):
    module = _load_script("speedaf_full_uat_probe.py")
    output = tmp_path / "full-uat-report.json"
    calls: list[tuple[str, dict]] = []

    class FakeFact:
        ok = True
        fact_evidence_present = True
        pii_redacted = True
        tool_status = "success"
        failure_reason = None
        status = "1"
        status_label = "status:1"

        def metadata_payload(self):
            return {"tracking_number_hash": "sha256:redacted", "raw_tracking_number_exposed": False}

    class FakeAdapter:
        def query_order_tracking_fact(self, *, waybill_code, caller_id):
            calls.append(("order_query", {"waybill_code": waybill_code, "caller_id": caller_id}))
            return FakeFact()

        def query_waybills_by_caller(self, *, caller_id, country_code):
            calls.append(("waybill_code_query", {"caller_id": caller_id, "country_code": country_code}))
            candidate = SimpleNamespace(waybill_code="CH020000006856", suffix="06856")
            return SimpleNamespace(ok=True, failure_reason=None, candidates=(candidate,))

    class FakeActionService:
        def create_work_order(self, *, waybill_code, caller_id, work_order_type, description):
            calls.append(
                (
                    "work_order_create",
                    {
                        "waybill_code": waybill_code,
                        "caller_id": caller_id,
                        "work_order_type": work_order_type,
                        "description": description,
                    },
                )
            )
            return SimpleNamespace(
                ok=True,
                status="created",
                error_code=None,
                safe_payload={"external_id": "wo-redacted", "debug": "CH020000006856 41000000000 PK000023"},
            )

        def submit_update_address_flow(self, *, waybill_code, caller_id, whatsapp_phone):
            calls.append(
                (
                    "address_update",
                    {"waybill_code": waybill_code, "caller_id": caller_id, "whatsapp_phone": whatsapp_phone},
                )
            )
            return SimpleNamespace(
                ok=True,
                status="success",
                error_code=None,
                safe_payload={"request": "redacted", "debug": "+41000009999"},
            )

        def cancel_order(self, *, waybill_code, caller_id, reason_code):
            calls.append(
                (
                    "cancel_order",
                    {"waybill_code": waybill_code, "caller_id": caller_id, "reason_code": reason_code},
                )
            )
            return SimpleNamespace(
                ok=True,
                status="success",
                error_code=None,
                safe_payload={"request": "redacted", "debug": "EXbSJrzZ"},
            )

        def send_voice_callback(self, payload):
            calls.append(("voice_callback", payload))
            return SimpleNamespace(
                ok=True,
                status="success",
                error_code=None,
                safe_payload={"request": "redacted", "debug": "CH020000006856"},
            )

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
    monkeypatch.setattr(module, "SpeedafActionService", FakeActionService)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "speedaf_full_uat_probe.py",
            "--waybill-code",
            "CH020000006856",
            "--caller-id",
            "41000000000",
            "--whatsapp-phone",
            "+41000009999",
            "--cancel-reason",
            "CC01",
            "--country-code",
            "CH",
            "--write-ack",
            module.WRITE_ACK,
            "--output-json",
            str(output),
        ],
    )

    code = module.main()

    assert code == 0
    assert [name for name, _payload in calls] == [
        "order_query",
        "waybill_code_query",
        "work_order_create",
        "address_update",
        "cancel_order",
        "voice_callback",
    ]
    assert calls[2][1]["description"] == "Nexus full UAT probe delivery follow-up"
    assert calls[4][1]["reason_code"] == "CC01"
    assert calls[5][1]["isTransferredToHuman"] == 1
    text = output.read_text(encoding="utf-8")
    for sensitive in ("CH020000006856", "41000000000", "+41000009999", "PK000023", "EXbSJrzZ"):
        assert sensitive not in text
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["write_acknowledged"] is True
    assert payload["redaction_guard"]["replacement_count"] >= 5
    for name in (
        "order_query",
        "waybill_code_query",
        "work_order_create",
        "address_update",
        "cancel_order",
        "voice_callback",
    ):
        assert payload["checks"][name]["ok"] is True
