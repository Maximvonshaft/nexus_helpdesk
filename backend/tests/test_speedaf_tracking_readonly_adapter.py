from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "scripts" / "speedaf_tracking_readonly_adapter.py"
BRIDGE_PATH = ROOT / "scripts" / "openclaw_bridge_server.js"


def _load_adapter_module():
    spec = importlib.util.spec_from_file_location("speedaf_tracking_readonly_adapter", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adapter_success_masks_tracking_and_redacts_pii():
    module = _load_adapter_module()

    def fake_track_query(tracking_number: str, *, dry_run: bool = False, debug: bool = False):
        assert tracking_number == "PK120053679836"
        return {"ok": True, "data": {"decrypted_response": {"data": [{"mailNo": tracking_number}]}}}

    def fake_analyze(_payload: dict):
        return {
            "timeline": [
                {
                    "time_utc": "2026-03-24T17:40:55Z",
                    "milestone": "delivery_out",
                    "action": "4",
                    "actionName": "派送中",
                    "raw": {"receiverCountryCode": "PK", "message": "Courier John at PK FIN Center"},
                },
                {
                    "time_utc": "2026-03-24T19:40:55Z",
                    "milestone": "delivered",
                    "action": "5",
                    "actionName": "Collected",
                    "raw": {
                        "receiverCountryCode": "PK",
                        "message": "Delivered to John Smith at PK FIN Center",
                        "pictureUrl": "https://example.test/pod.jpg",
                        "proof_tag": "[[proof]]",
                    },
                },
            ],
            "risk": {"escalate_required": False},
        }

    result = module.lookup_tracking_readonly_adapter(
        {"tracking_number": "PK120053679836", "source": "bridge_internal"},
        track_query_fn=fake_track_query,
        analyze_payload_fn=fake_analyze,
        status_map={"4": {"label": "Out for delivery"}, "5": {"label": "Delivered"}},
        should_escalate_fn=lambda raw: False,
    )

    assert result["ok"] is True
    assert result["source"] == "speedaf_readonly_adapter"
    assert result["tracking_number_masked"] == "****9836"
    assert result["tracking_hash"].startswith("sha256:")
    assert result["raw_included"] is False
    assert result["pii_redacted"] is True
    assert result["latest_status"] == "Delivered"
    assert result["latest_milestone"] == "delivered"
    assert result["latest_event_location_safe"] == "PK delivery area"
    blob = json.dumps(result, ensure_ascii=False)
    assert "John Smith" not in blob
    assert "PK FIN Center" not in blob
    assert "pictureUrl" not in blob
    assert "proof_tag" not in blob


def test_adapter_maps_missing_and_invalid_tracking_number():
    module = _load_adapter_module()

    missing = module.lookup_tracking_readonly_adapter({})
    assert missing["ok"] is False
    assert missing["error"] == "missing_tracking_number"

    invalid = module.lookup_tracking_readonly_adapter({"tracking_number": "abc"})
    assert invalid["ok"] is False
    assert invalid["error"] == "invalid_tracking_number"


def test_adapter_maps_upstream_timeout_and_error_and_no_tracking_info():
    module = _load_adapter_module()

    timeout_result = module.lookup_tracking_readonly_adapter(
        {"tracking_number": "PK120053679836"},
        track_query_fn=lambda *_args, **_kwargs: {"ok": False, "error": "network_error:timed out", "layer": "timeout"},
        analyze_payload_fn=lambda payload: payload,
        status_map={},
        should_escalate_fn=lambda raw: False,
    )
    assert timeout_result["error"] == "upstream_timeout"

    upstream_error = module.lookup_tracking_readonly_adapter(
        {"tracking_number": "PK120053679836"},
        track_query_fn=lambda *_args, **_kwargs: {"ok": False, "error": "http_error", "layer": "protocol"},
        analyze_payload_fn=lambda payload: payload,
        status_map={},
        should_escalate_fn=lambda raw: False,
    )
    assert upstream_error["error"] == "upstream_error"

    no_info = module.lookup_tracking_readonly_adapter(
        {"tracking_number": "PK120053679836"},
        track_query_fn=lambda *_args, **_kwargs: {"ok": True, "data": {}},
        analyze_payload_fn=lambda payload: {},
        status_map={},
        should_escalate_fn=lambda raw: False,
    )
    assert no_info["error"] == "no_tracking_info"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_bridge(port: int, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 10
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"bridge exited early: {proc.stdout.read()} {proc.stderr.read()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"bridge did not become ready: {last_error}")


def _start_bridge_server() -> tuple[subprocess.Popen[str], int]:
    port = _free_port()
    temp_dir = tempfile.TemporaryDirectory()
    tmp = Path(temp_dir.name)
    runtime_module = tmp / "fake-gateway-runtime.mjs"
    runtime_module.write_text(
        "export class GatewayClient { constructor(opts){ this.opts=opts; } start(){ if (this.opts?.onHelloOk) this.opts.onHelloOk({protocol:'test', policy:{tickIntervalMs:1000}}); } request(){ throw new Error('not_implemented'); } }\n",
        encoding="utf-8",
    )
    config_path = tmp / "openclaw.json"
    config_path.write_text(json.dumps({"gateway": {"port": 18789, "auth": {"token": "test-token"}}}), encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "OPENCLAW_CONFIG_PATH": str(config_path),
        "OPENCLAW_BRIDGE_PORT": str(port),
        "OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED": "false",
        "OPENCLAW_BRIDGE_ALLOW_WRITES": "false",
        "OPENCLAW_BRIDGE_TRACKING_LOOKUP_METHOD": "readonly_adapter",
        "OPENCLAW_BRIDGE_TRACKING_LOOKUP_ADAPTER": "speedaf_tracking_readonly_adapter",
        "OPENCLAW_GATEWAY_RUNTIME_MODULE": str(runtime_module),
    })
    proc = subprocess.Popen(
        ["node", str(BRIDGE_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    proc._temp_dir = temp_dir  # type: ignore[attr-defined]
    _wait_for_bridge(port, proc)
    return proc, port


def _stop_bridge_server(proc: subprocess.Popen[str]) -> None:
    temp_dir = getattr(proc, "_temp_dir", None)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        proc.kill()
    if temp_dir is not None:
        temp_dir.cleanup()


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else "{}"
        return exc.code, json.loads(body or "{}")


def test_bridge_endpoint_feature_gate_and_send_message_safety_valve():
    proc, port = _start_bridge_server()
    try:
        status, payload = _post_json(f"http://127.0.0.1:{port}/tools/speedaf_lookup", {"tracking_number": "PK120053679836"})
        assert status == 403
        assert payload["error"] == "bridge_tracking_lookup_disabled"

        status, payload = _post_json(f"http://127.0.0.1:{port}/tools/speedaf_lookup", {})
        assert status == 400
        assert payload["error"] == "missing_required_fields"
        assert payload["missing"] == ["tracking_number"]

        status, payload = _post_json(f"http://127.0.0.1:{port}/send-message", {"channel": "telegram", "target": "123", "body": "hi"})
        assert status == 403
        assert payload["error"] == "bridge_writes_disabled"
    finally:
        _stop_bridge_server(proc)
