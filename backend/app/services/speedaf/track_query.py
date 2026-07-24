from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

from ..tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from .redactor import redact_mapping, safe_waybill_payload
from .schemas import SpeedafMcpNormalizedError
from .status_map import order_status_context, tracking_event_needs_human_review

TRACK_QUERY_PATH = "/open-api/express/track/query"
TRACK_QUERY_TOOL_NAME = "speedaf.express.track.query"
TRACK_QUERY_SOURCE = "speedaf_api.express_track_query"
_DEFAULT_IV = bytes.fromhex("1234567890abcdef")
_TIMEZONE_OFFSET_BY_CODE = {1: 2, 8: 8, None: 0}
_MILESTONE_BY_ACTION: dict[tuple[str, str | None], str] = {
    ("1", None): "pickup",
    ("150", None): "origin_warehouse_inbound",
    ("181", None): "origin_consolidated",
    ("190", None): "origin_warehouse_departed",
    ("191", None): "origin_handover_completed",
    ("220", None): "flight_departed",
    ("230", None): "flight_arrived",
    ("360", "360"): "customs_started",
    ("360", "36003"): "customs_inspection",
    ("370", "370"): "customs_completed",
    ("401", None): "customs_exception",
    ("375", None): "destination_hub_arrived",
    ("4", None): "out_for_delivery",
    ("5", None): "delivered",
    ("18", None): "pickup_point",
}


@dataclass(frozen=True)
class SpeedafTrackQueryConfig:
    enabled: bool
    base_url: str
    app_code: str | None
    secret_key: str | None
    timeout_seconds: int = 8
    content_type: str = "text/plain"

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.app_code and self.secret_key)


@dataclass(frozen=True)
class SpeedafTrackQueryEnvelope:
    path: str
    query: dict[str, Any]
    body: dict[str, Any]
    headers: dict[str, str]
    timestamp_ms: int
    data_string: str
    sign: str


@dataclass(frozen=True)
class SpeedafTrackEvent:
    action: str | None = None
    action_name: str | None = None
    message: str | None = None
    msg_eng: str | None = None
    msg_loc: str | None = None
    msg_sh: str | None = None
    sub_action: str | None = None
    event_time: str | None = None
    timezone: int | None = None
    return_flag: str | None = None
    scan_source: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpeedafTrackEvent":
        timezone_raw = payload.get("timezone")
        try:
            timezone = int(timezone_raw) if timezone_raw not in (None, "") else None
        except (TypeError, ValueError):
            timezone = None
        return cls(
            action=_clean(payload.get("action")),
            action_name=_clean(payload.get("actionName") or payload.get("action_name")),
            message=_clean(payload.get("message")),
            msg_eng=_clean(payload.get("msgEng") or payload.get("msg_eng")),
            msg_loc=_clean(payload.get("msgLoc") or payload.get("msg_loc")),
            msg_sh=_clean(payload.get("msgSh") or payload.get("msg_sh")),
            sub_action=_clean(payload.get("subAction") or payload.get("sub_action")),
            event_time=_clean(payload.get("time") or payload.get("eventTime") or payload.get("event_time")),
            timezone=timezone,
            return_flag=_clean(payload.get("returnFlag") or payload.get("return_flag")),
            scan_source=_clean(payload.get("scanSource") or payload.get("scan_source")),
        )

    @property
    def customer_description(self) -> str | None:
        return self.msg_eng or self.msg_loc or self.message or self.action_name or self.action

    def to_tracking_event(self) -> TrackingFactEvent:
        return TrackingFactEvent(
            event_time=self.event_time,
            location=None,
            description=self.customer_description,
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "action": self.action,
                "action_name": self.action_name,
                "description": self.customer_description,
                "event_time": self.event_time,
                "timezone": self.timezone,
                "return_flag": self.return_flag,
                "scan_source": self.scan_source,
            }.items()
            if value not in (None, "")
        }


def _milestone_for_event(event: SpeedafTrackEvent) -> str | None:
    action = str(event.action or "").strip()
    sub_action = str(event.sub_action or "").strip() or None
    if not action:
        return None
    return _MILESTONE_BY_ACTION.get((action, sub_action)) or _MILESTONE_BY_ACTION.get((action, None))


def _parse_speedaf_event_time(value: str | None, timezone_code: int | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            local = datetime.strptime(value, fmt)
            offset = _TIMEZONE_OFFSET_BY_CODE.get(timezone_code, 0)
            return local - timedelta(hours=offset)
        except ValueError:
            continue
    return None


def _first_time(events: list[dict[str, Any]], milestones: set[str], *, reverse: bool = False) -> datetime | None:
    iterable = reversed(events) if reverse else events
    for item in iterable:
        if item.get("milestone") in milestones and isinstance(item.get("time_utc"), datetime):
            return item["time_utc"]
    return None


def _duration_hours(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end or end <= start:
        return None
    return round((end - start).total_seconds() / 3600, 3)


def speedaf_track_lifecycle_summary(events: tuple[SpeedafTrackEvent, ...]) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        time_utc = _parse_speedaf_event_time(event.event_time, event.timezone)
        milestone = _milestone_for_event(event)
        if not time_utc and not milestone and not event.action:
            continue
        timeline.append(
            {
                "index": index,
                "time_utc": time_utc,
                "milestone": milestone,
                "action": event.action,
                "sub_action": event.sub_action,
            }
        )
    timeline.sort(key=lambda item: item["time_utc"] or datetime.max)
    if not timeline:
        return {}

    origin_start = _first_time(timeline, {"pickup", "origin_warehouse_inbound"})
    origin_end = _first_time(timeline, {"flight_departed", "origin_handover_completed"}, reverse=True)
    flight_dep = _first_time(timeline, {"flight_departed"})
    flight_arr = _first_time(timeline, {"flight_arrived"})
    customs_start = _first_time(timeline, {"customs_started"})
    customs_inspection = _first_time(timeline, {"customs_inspection"})
    customs_completed = _first_time(timeline, {"customs_completed"})
    destination_start = _first_time(timeline, {"destination_hub_arrived"}) or _first_time(timeline, {"customs_completed"})
    delivered = _first_time(timeline, {"delivered", "pickup_point"})

    durations = {
        key: value
        for key, value in {
            "origin_hours": _duration_hours(origin_start, origin_end),
            "flight_hours": _duration_hours(flight_dep, flight_arr),
            "customs_hours": _duration_hours(customs_start, customs_completed),
            "inspection_hours": _duration_hours(customs_inspection, customs_completed),
            "last_mile_hours": _duration_hours(destination_start, delivered),
            "total_transit_hours": _duration_hours(
                next((item["time_utc"] for item in timeline if isinstance(item.get("time_utc"), datetime)), None),
                next((item["time_utc"] for item in reversed(timeline) if isinstance(item.get("time_utc"), datetime)), None),
            ),
        }.items()
        if value is not None
    }
    latest = timeline[-1]
    customs_hours = durations.get("customs_hours")
    text_escalate_required = any(tracking_event_needs_human_review(event) for event in events)
    escalate_reasons: list[str] = []
    if latest.get("action") == "401":
        escalate_reasons.append("customs_exception_status")
    if isinstance(customs_hours, (int, float)) and customs_hours > 240:
        escalate_reasons.append("customs_duration_over_240_hours")
    if text_escalate_required:
        escalate_reasons.append("tracking_event_exception_language")
    escalate_required = bool(escalate_reasons)
    return {
        "latest_milestone": latest.get("milestone"),
        "latest_action": latest.get("action"),
        "latest_sub_action": latest.get("sub_action"),
        "durations": durations,
        "risk": {"escalate_required": escalate_required, "reasons": escalate_reasons},
    }


@dataclass(frozen=True)
class SpeedafTrackHistory:
    mail_no: str | None
    events: tuple[SpeedafTrackEvent, ...]
    raw_safe: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpeedafTrackHistory":
        tracks = payload.get("tracks")
        events: list[SpeedafTrackEvent] = []
        if isinstance(tracks, list):
            for item in tracks:
                if isinstance(item, dict):
                    events.append(SpeedafTrackEvent.from_payload(item))
        return cls(
            mail_no=_clean(payload.get("mailNo") or payload.get("mail_no") or payload.get("waybillCode")),
            events=tuple(events),
            raw_safe=redact_mapping(payload),
        )

    def to_tracking_fact(self) -> TrackingFactResult:
        tracking_events = [event.to_tracking_event() for event in self.events if event.to_tracking_event().is_present()]
        latest_event = tracking_events[0] if tracking_events else None
        latest_domain_event = self.events[0] if self.events else None
        status = latest_domain_event.action if latest_domain_event else None
        status_label = latest_domain_event.customer_description if latest_domain_event else None
        lifecycle_summary = speedaf_track_lifecycle_summary(self.events)
        status_context = dict(order_status_context(status))
        risk = lifecycle_summary.get("risk") if isinstance(lifecycle_summary.get("risk"), dict) else {}
        if risk.get("escalate_required") is True:
            status_context["needs_human_review"] = True
            status_context.setdefault("handling_hint", "Human review may be needed for this tracking event.")
        return TrackingFactResult(
            ok=True,
            tracking_number=self.mail_no,
            status=status,
            status_label=status_label,
            latest_event=latest_event,
            events_summary=tracking_events[:5],
            checked_at=None,
            source=TRACK_QUERY_SOURCE,
            tool_name=TRACK_QUERY_TOOL_NAME,
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=bool(self.events),
            lifecycle_summary=lifecycle_summary,
            status_context=status_context,
        )


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 30) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_int_any(names: tuple[str, ...], default: int, *, minimum: int = 1, maximum: int = 30) -> int:
    for name in names:
        if os.getenv(name) not in (None, ""):
            return _env_int(name, default, minimum=minimum, maximum=maximum)
    return _env_int(names[0], default, minimum=minimum, maximum=maximum)


def _track_query_base_url() -> str:
    configured = (os.getenv("SPEEDAF_TRACK_QUERY_BASE_URL") or os.getenv("SPEEDAF_BASE_URL") or "https://apis.speedaf.com").strip()
    if configured.endswith("/open-api/mcp"):
        configured = configured[: -len("/open-api/mcp")]
    return (configured or "https://apis.speedaf.com").rstrip("/")


def load_speedaf_track_query_config() -> SpeedafTrackQueryConfig:
    return SpeedafTrackQueryConfig(
        enabled=_env_bool("SPEEDAF_TRACK_QUERY_ENABLED", False),
        base_url=_track_query_base_url(),
        app_code=os.getenv("SPEEDAF_TRACK_QUERY_APP_CODE") or os.getenv("SPEEDAF_APP_CODE"),
        secret_key=os.getenv("SPEEDAF_TRACK_QUERY_SECRET_KEY") or os.getenv("SPEEDAF_SECRET_KEY"),
        timeout_seconds=_env_int_any(("SPEEDAF_TRACK_QUERY_TIMEOUT_SECONDS", "SPEEDAF_TIMEOUT"), 8),
        content_type=(os.getenv("SPEEDAF_TRACK_QUERY_CONTENT_TYPE", "text/plain").strip() or "text/plain"),
    )


class SpeedafTrackQueryError(RuntimeError):
    def __init__(self, error: SpeedafMcpNormalizedError, *, safe_payload: dict[str, Any] | None = None) -> None:
        self.error = error
        self.safe_payload = safe_payload or {}
        super().__init__(f"speedaf_track_query_error:{error.code}")


class SpeedafTrackQueryClient:
    """Client for /open-api/express/track/query.

    This interface is intentionally separate from the MCP order-query client.
    It uses signed requests and accepts both production response variants:
    plaintext JSON data strings and DES-encrypted Base64 data strings.
    """

    def __init__(self, config: SpeedafTrackQueryConfig | None = None, *, http_client: httpx.Client | None = None) -> None:
        self.config = config or load_speedaf_track_query_config()
        self._http_client = http_client

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def build_envelope(self, mail_no_list: list[str]) -> SpeedafTrackQueryEnvelope:
        if not self.config.configured:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="speedaf_track_query_not_configured", message="Speedaf track query is disabled or missing appCode/secretKey", retryable=False))
        cleaned = [mail_no.strip().upper() for mail_no in mail_no_list if mail_no and mail_no.strip()]
        if not cleaned:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="missing_mail_no", message="At least one mailNo is required", retryable=False))
        timestamp_ms = self._timestamp_ms()
        data_string = json.dumps({"mailNoList": cleaned}, ensure_ascii=False, separators=(",", ":"))
        sign = build_speedaf_track_sign(str(timestamp_ms), self.config.secret_key or "", data_string)
        return SpeedafTrackQueryEnvelope(
            path=TRACK_QUERY_PATH,
            query={"appCode": self.config.app_code, "timestamp": timestamp_ms},
            body={"data": data_string, "sign": sign},
            headers={"Content-Type": self.config.content_type, "Accept": "application/json"},
            timestamp_ms=timestamp_ms,
            data_string=data_string,
            sign=sign,
        )

    def query_history(self, mail_no: str) -> SpeedafTrackHistory:
        envelope = self.build_envelope([mail_no])
        safe_request = {
            "path": envelope.path,
            "query": {"appCode": {"redacted": True}, "timestamp": envelope.timestamp_ms},
            "body": {**safe_waybill_payload(mail_no), "sign": _safe_sign(envelope.sign), "data_mode": "string"},
            "content_type": envelope.headers.get("Content-Type"),
        }
        try:
            client = self._http_client or httpx.Client(timeout=self.config.timeout_seconds)
            response = client.post(self._url(envelope.path), params=envelope.query, json=envelope.body, headers=envelope.headers)
        except httpx.TimeoutException as exc:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="timeout", message=str(exc), retryable=True), safe_payload=safe_request) from exc
        except httpx.HTTPError as exc:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="http_error", message=str(exc), retryable=True), safe_payload=safe_request) from exc
        finally:
            if self._http_client is None:
                try:
                    client.close()  # type: ignore[name-defined]
                except Exception:
                    pass
        try:
            raw = response.json()
        except ValueError:
            raw = {"raw_text": response.text[:500]}
        if not isinstance(raw, dict):
            raw = {"result": raw}
        if not 200 <= response.status_code < 300:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code=f"http_{response.status_code}", message=None, retryable=response.status_code >= 500, http_status=response.status_code), safe_payload={"request": safe_request, "response": redact_mapping(raw)})
        if raw.get("success") is not True:
            error = raw.get("error")
            error_code = None
            error_message = None
            if isinstance(error, dict):
                error_code = _clean(error.get("code"))
                error_message = _clean(error.get("message") or error.get("msg"))
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code=error_code or "track_query_failed", message=error_message, retryable=False, http_status=response.status_code), safe_payload={"request": safe_request, "response": redact_mapping(raw)})
        response_data = raw.get("data")
        if not isinstance(response_data, str) or not response_data.strip():
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="missing_encrypted_data", message="Speedaf track query response data is empty", retryable=False, http_status=response.status_code), safe_payload={"request": safe_request, "response": redact_mapping(raw)})
        parsed_data = parse_speedaf_track_response_data(response_data, self.config.secret_key or "")
        histories = parse_speedaf_track_histories(parsed_data)
        requested = mail_no.strip().upper()
        for history in histories:
            if (history.mail_no or "").strip().upper() == requested:
                return history
        if histories:
            return histories[0]
        raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="track_history_empty", message="Speedaf track query returned no track histories", retryable=False, http_status=response.status_code), safe_payload={"request": safe_request, "response": redact_mapping(raw)})


def build_speedaf_track_sign(timestamp_ms: str, secret_key: str, data_string: str) -> str:
    # The provider offers no SHA-2 alternative for this authenticated wire signature.
    # Speedaf wire protocol mandates MD5; this is compatibility signing, not password/security hashing.
    payload = f"{timestamp_ms}{secret_key}{data_string}".encode("utf-8")
    # codeql[py/weak-sensitive-data-hashing]
    digest = hashlib.md5(payload, usedforsecurity=False)
    return digest.hexdigest()


def decrypt_speedaf_track_data(data_b64: str, secret_key: str) -> Any:
    key = secret_key.encode("utf-8")
    if len(key) != 8:
        raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="invalid_des_key_length", message="Speedaf track query secretKey must be exactly 8 bytes for DES-compatible decryption", retryable=False))
    try:
        encrypted = base64.b64decode(data_b64)
    except Exception as exc:
        raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="invalid_base64_data", message="Speedaf track query response data is not valid base64", retryable=False)) from exc
    try:
        # Speedaf response wire format mandates DES-compatible CBC; transport remains HTTPS and disabled by default.
        cipher = Cipher(
            TripleDES(key),  # nosec B304
            modes.CBC(_DEFAULT_IV),
        )
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(64).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        text = plaintext.decode("utf-8")
        return json.loads(text)
    except Exception as exc:
        raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="des_decrypt_failed", message="Failed to decrypt Speedaf track query response data", retryable=False)) from exc


def parse_speedaf_track_response_data(data: str, secret_key: str) -> Any:
    stripped = (data or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SpeedafTrackQueryError(SpeedafMcpNormalizedError(code="plain_json_decode_failed", message="Speedaf track query response data is not valid JSON", retryable=False)) from exc
    return decrypt_speedaf_track_data(stripped, secret_key)


def parse_speedaf_track_histories(payload: Any) -> tuple[SpeedafTrackHistory, ...]:
    source = payload
    if isinstance(source, dict):
        for key in ("data", "result", "list", "records", "items"):
            if isinstance(source.get(key), list):
                source = source[key]
                break
        else:
            if source.get("mailNo") or source.get("tracks"):
                source = [source]
    if not isinstance(source, list):
        return ()
    histories: list[SpeedafTrackHistory] = []
    for item in source:
        if isinstance(item, dict):
            history = SpeedafTrackHistory.from_payload(item)
            if history.mail_no or history.events:
                histories.append(history)
    return tuple(histories)


def _safe_sign(sign: str) -> dict[str, Any]:
    if not sign:
        return {"present": False}
    return {"present": True, "prefix": sign[:4], "suffix": sign[-4:], "length": len(sign)}
