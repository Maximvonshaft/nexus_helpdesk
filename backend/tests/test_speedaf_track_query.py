from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.speedaf.track_query import (
    SpeedafTrackQueryClient,
    SpeedafTrackQueryConfig,
    SpeedafTrackQueryError,
    build_speedaf_track_sign,
    decrypt_speedaf_track_data,
    parse_speedaf_track_histories,
)
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


IV = bytes.fromhex("1234567890abcdef")
SECRET = "99nhSaBD"


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
        secret_key=SECRET,
        timeout_seconds=8,
        content_type="text/plain",
    )


def _encrypt_payload(payload) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padder = padding.PKCS7(64).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.TripleDES(SECRET.encode("utf-8")), modes.CBC(IV))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def test_build_speedaf_track_sign_matches_contract() -> None:
    data = '{"mailNoList":["MK000179196R"]}'
    assert build_speedaf_track_sign("1774518097430", SECRET, data) == "e73547d2d461eea3fd344c4b5cd67603"


def test_build_envelope_uses_string_data_and_sign_without_customer_code() -> None:
    client = SpeedafTrackQueryClient(_cfg())
    envelope = client.build_envelope(["mk000179196r"])

    assert envelope.path == "/open-api/express/track/query"
    assert envelope.query["appCode"] == "CH000001"
    assert "timestamp" in envelope.query
    assert envelope.body["data"] == '{"mailNoList":["MK000179196R"]}'
    assert envelope.body["sign"] == build_speedaf_track_sign(str(envelope.timestamp_ms), SECRET, envelope.body["data"])
    assert "customerCode" not in envelope.body["data"]
    assert envelope.headers["Content-Type"] == "text/plain"


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

    parsed = decrypt_speedaf_track_data(encrypted, SECRET)
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
    assert fake_http.last_json["sign"] == build_speedaf_track_sign(str(fake_http.last_params["timestamp"]), SECRET, fake_http.last_json["data"])
    assert fake_http.last_headers["Content-Type"] == "text/plain"


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
