from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SpeedafMcpConfig:
    enabled: bool
    base_url: str
    app_code: str | None
    secret_key: str | None
    timeout_seconds: int = 8
    country_code_default: str = "CH"
    content_type: str = "text/plain"
    data_mode: str = "string"
    require_sign: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.app_code)


@dataclass(frozen=True)
class SpeedafMcpEnvelope:
    path: str
    query: dict[str, Any]
    body: dict[str, Any]
    headers: dict[str, str]
    timestamp_ms: int


@dataclass(frozen=True)
class SpeedafMcpNormalizedError:
    code: str
    message: str | None = None
    retryable: bool = False
    http_status: int | None = None


@dataclass(frozen=True)
class SpeedafWaybillCandidate:
    waybill_code: str
    suffix: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpeedafWaybillCandidate | None":
        code = str(payload.get("waybillCode") or payload.get("waybill_code") or "").strip()
        if not code:
            return None
        suffix = str(payload.get("waybillCodeSuffix") or payload.get("suffix") or code[-4:]).strip() or None
        return cls(waybill_code=code, suffix=suffix)


@dataclass(frozen=True)
class SpeedafOrderFact:
    waybill_code: str | None = None
    status: str | None = None
    status_label: str | None = None
    order_class: str | None = None
    order_class_label: str | None = None
    current_branch: str | None = None
    estimated_delivery_time: str | None = None
    checked_at: str | None = None
    raw_safe: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeedafWorkOrderResult:
    ok: bool
    status: str
    external_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    safe_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeedafActionRequest:
    waybill_code: str
    caller_id: str
    request_id: str | None = None
    ticket_id: int | None = None
    conversation_id: int | None = None
