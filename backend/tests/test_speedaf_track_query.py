from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

from app.services.speedaf.track_query import (
    SpeedafTrackEvent,
    SpeedafTrackQueryClient,
    SpeedafTrackQueryConfig,
    SpeedafTrackQueryError,
    build_speedaf_track_sign,
    decrypt_speedaf_track_data,
    load_speedaf_track_query_config,
    parse_speedaf_track_response_data,
    parse_speedaf_track_histories,
    speedaf_track_lifecycle_summary,
)
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


IV = bytes.fromhex("1234567890abcdef")
WIRE_PROTOCOL_KEY = "99nhSaBD"


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_params = None
        self.last_json = None
        self.last_headers = None

    def post(self, url, *, params=None, json=None, headers=None):
        self.last_params = params
        self.last_json = json
        self.last_headers = headers
        return _FakeResponse(self.payload)


def _cfg() -> SpeedafTrackQueryConfig:
    return SpeedafTrackQueryConfig(
        enabled=True,
        base_url="https://apis.speedaf.com",
        app_code="CH000001",
        secret_key=WIRE_PROTOCOL_KEY,
        timeout_seconds=8,
        content_type="text/plain",
    )


def _encrypt_payload(payload) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padder = padding.PKCS7(64).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(TripleDES(WIRE_PROTOCOL_KEY.encode("utf-8")), modes.CBC(IV))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def test_build_speedaf_track_sign_matches_contract() -> None:
    data = '{"mailNoList":["MK000179196R"]}'
    assert build_speedaf_track_sign("1774518097430", WIRE_PROTOCOL_KEY, data) == "c541ae0a0106f991a2be06ed6deac988"


def test_build_envelope_uses_string_data_and_sign_without_customer_code() -> None:
    client = SpeedafTrackQueryClient(_cfg())
    envelope = client.build_envelope(["mk000179196r"])

    assert envelope.path == "/open-api/express/track/query"
    assert envelope.query["appCode"] == "CH000001"
    assert "timestamp" in envelope.query
    assert envelope.body["data"] == '{"mailNoList":["MK000179196R"]}'
    assert envelope.body["sign"] == build_speedaf_track_sign(str(envelope.timestamp_ms), WIRE_PROTOCOL_KEY, envelope.body["data"])
    assert "customerCode" not in envelope.body["data"]
    assert envelope.headers["Content-Type"] == "text/plain"


def test_load_track_query_config_accepts_support_agent_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("SPEEDAF_TRACK_QUERY_ENABLED", "true")
    monkeypatch.delenv("SPEEDAF_TRACK_QUERY_BASE_URL", raising=False)
    monkeypatch.delenv("SPEEDAF_TRACK_QUERY_APP_CODE", raising=False)
    monkeypatch.delenv("SPEEDAF_TRACK_QUERY_SECRET_KEY", raising=False)
    monkeypatch.delenv("SPEEDAF_TRACK_QUERY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("SPEEDAF_BASE_URL", "https://apis.speedaf.com/open-api/mcp")
    monkeypatch.setenv("SPEEDAF_APP_CODE", "CH000001")
    monkeypatch.setenv("SPEEDAF_SECRET_KEY", WIRE_PROTOCOL_KEY)
    monkeypatch.setenv("SPEEDAF_TIMEOUT", "20")

    config = load_speedaf_track_query_config()

    assert config.configured is True
    assert config.base_url == "https://apis.speedaf.com"
    assert config.app_code == "CH000001"
    assert config.secret_key == WIRE_PROTOCOL_KEY
    assert config.timeout_seconds == 20


def test_decrypt_speedaf_track_data_and_parse_histories() -> None:
    decrypted = [
        {
            "mailNo": "MK000179196R",
            "tracks": [
                {
                    "action": "-20",
                    "actionName": "退货出仓",
                    "message": "国内退回客户",
                    "msgEng": "Returned to customer in origin country",
                    "time": "2026-03-19 19:35:04",
                    "timezone": 8,
                    "returnFlag": "1",
                    "scanSource": "SYSTEM",
                },
                {
                    "action": "150",
                    "actionName": "入库",
                    "message": "[广州花都仓] 订单已操作入库",
                    "time": "2026-03-17 16:11:21",
                    "timezone": 8,
                    "returnFlag": "0",
                },
            ],
        }
    ]
    encrypted = _encrypt_payload(decrypted)

    parsed = decrypt_speedaf_track_data(encrypted, WIRE_PROTOCOL_KEY)
    histories = parse_speedaf_track_histories(parsed)
    tracking = histories[0].to_tracking_fact()
    summary = tracking.prompt_summary()

    assert parsed == decrypted
    assert len(histories) == 1
    assert histories[0].mail_no == "MK000179196R"
    assert len(histories[0].events) == 2
    assert tracking.ok is True
    assert tracking.fact_evidence_present is True
    assert tracking.tool_name == "speedaf.express.track.query"
    assert tracking.source == "speedaf_api.express_track_query"
    assert tracking.status == "-20"
    assert tracking.status_label == "Returned to customer in origin country"
    assert "Returned to customer in origin country" in summary
    assert "Speedaf status code: -20" in summary
    assert "SYSTEM" not in summary


def test_query_history_uses_encrypted_success_payload() -> None:
    decrypted = [
        {
            "mailNo": "MK000179196R",
            "tracks": [
                {
                    "action": "150",
                    "actionName": "入库",
                    "message": "[广州花都仓] 订单已操作入库",
                    "time": "2026-03-17 16:11:21",
                    "timezone": 8,
                }
            ],
        }
    ]
    fake_http = _FakeHttpClient({"success": True, "error": None, "data": _encrypt_payload(decrypted)})
    client = SpeedafTrackQueryClient(_cfg(), http_client=fake_http)

    history = client.query_history("MK000179196R")

    assert history.mail_no == "MK000179196R"
    assert len(history.events) == 1
    assert fake_http.last_json["data"] == '{"mailNoList":["MK000179196R"]}'
    assert fake_http.last_json["sign"] == build_speedaf_track_sign(str(fake_http.last_params["timestamp"]), WIRE_PROTOCOL_KEY, fake_http.last_json["data"])
    assert fake_http.last_headers["Content-Type"] == "text/plain"


def test_query_history_accepts_plaintext_json_data_string() -> None:
    payload = [
        {
            "mailNo": "MK000179196R",
            "tracks": [
                {
                    "action": "-20",
                    "msgEng": "Returned to customer in origin country",
                    "time": "2026-03-19 19:35:04",
                }
            ],
        }
    ]
    fake_http = _FakeHttpClient({"success": True, "error": None, "data": json.dumps(payload, separators=(",", ":"))})
    client = SpeedafTrackQueryClient(_cfg(), http_client=fake_http)

    history = client.query_history("MK000179196R")

    assert history.mail_no == "MK000179196R"
    assert len(history.events) == 1
    assert history.events[0].customer_description == "Returned to customer in origin country"


def test_parse_speedaf_track_response_data_keeps_encrypted_compatibility() -> None:
    payload = [{"mailNo": "MK000179196R", "tracks": []}]

    assert parse_speedaf_track_response_data(json.dumps(payload), WIRE_PROTOCOL_KEY) == payload
    assert parse_speedaf_track_response_data(_encrypt_payload(payload), WIRE_PROTOCOL_KEY) == payload


def test_track_lifecycle_summary_is_safe_runtime_context() -> None:
    events = (
        SpeedafTrackEvent(action="360", sub_action="360", event_time="2026-07-01 08:00:00", timezone=2),
        SpeedafTrackEvent(action="370", sub_action="370", event_time="2026-07-01 20:00:00", timezone=2),
        SpeedafTrackEvent(action="375", event_time="2026-07-02 08:00:00", timezone=2),
        SpeedafTrackEvent(action="4", event_time="2026-07-02 12:00:00", timezone=2),
        SpeedafTrackEvent(action="5", msg_eng="Delivered", event_time="2026-07-02 18:00:00", timezone=2),
    )

    summary = speedaf_track_lifecycle_summary(events)

    assert summary["latest_milestone"] == "delivered"
    assert summary["latest_action"] == "5"
    assert summary["durations"]["customs_hours"] == 12
    assert summary["durations"]["last_mile_hours"] == 10
    assert summary["risk"]["escalate_required"] is False


def test_track_lifecycle_customs_exception_marks_human_review_risk() -> None:
    history = parse_speedaf_track_histories(
        [
            {
                "mailNo": "CH120000005451",
                "tracks": [
                    {
                        "action": "401",
                        "msgEng": "Customs exception",
                        "time": "2026-07-01 10:00:00",
                        "timezone": 2,
                    }
                ],
            }
        ]
    )[0]

    fact = history.to_tracking_fact()
    summary = fact.prompt_summary()

    assert fact.lifecycle_summary["latest_milestone"] == "customs_exception"
    assert fact.lifecycle_summary["risk"]["escalate_required"] is True
    assert fact.status_context["label"] == "customs exception"
    assert "Lifecycle risk: human review may be required." in summary
    assert "Status risk: human review may be required." in summary


def test_track_lifecycle_exception_language_marks_human_review_risk() -> None:
    history = parse_speedaf_track_histories(
        [
            {
                "mailNo": "CH120000005451",
                "tracks": [
                    {
                        "action": "4",
                        "msgEng": "Delivery failed because the recipient could not be contacted",
                        "time": "2026-07-01 10:00:00",
                        "timezone": 2,
                    }
                ],
            }
        ]
    )[0]

    fact = history.to_tracking_fact()

    assert fact.lifecycle_summary["risk"]["escalate_required"] is True
    assert "tracking_event_exception_language" in fact.lifecycle_summary["risk"]["reasons"]
    assert fact.status_context["needs_human_review"] is True


def test_query_history_not_configured_error_is_safe() -> None:
    client = SpeedafTrackQueryClient(
        SpeedafTrackQueryConfig(
            enabled=False,
            base_url="https://apis.speedaf.com",
            app_code=None,
            secret_key=None,
        )
    )

    with pytest.raises(SpeedafTrackQueryError) as exc_info:
        client.query_history("MK000179196R")

    assert exc_info.value.error.code == "speedaf_track_query_not_configured"


def test_track_query_tool_contract_is_registered_as_read_only() -> None:
    contract = get_tool_contract("speedaf.express.track.query")

    assert contract is not None
    assert contract.classification == "read"
    assert contract.confirmation_required is False
    assert contract.allowed_auto_execution_mode == "policy_gated"
