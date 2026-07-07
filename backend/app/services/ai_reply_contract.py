from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..enums import ConversationState
from ..settings import get_settings

AI_REPLY_CONTRACT_V2 = "nexus.ai_reply.v2"
AI_REPLY_CONTRACT_V3 = "nexus.ai_reply.v3"

AI_ORIGINS = {"provider_runtime", "ai_runtime"}
HUMAN_ORIGIN = "human_agent"
FORBIDDEN_CUSTOMER_VISIBLE_ORIGINS = {"business_system", "tool_service", "knowledge_runtime", "safety_service"}
VALID_SAFETY_STATUSES = {"passed", "reviewed"}
VALID_AI_REPLY_CONTRACTS = {AI_REPLY_CONTRACT_V2, AI_REPLY_CONTRACT_V3}
VALID_REPLY_TYPES = {"answer", "clarifying_question", "handoff_notice", "null_reply"}
WEAK_RUNTIME_CONTRACT_SECRETS = {"", "change-me", "changeme", "replace-me", "replace_this", "secret", "default", "dev-only"}
HUMAN_REPLY_STATES = {
    ConversationState.human_owned,
    ConversationState.ready_to_reply,
}


@dataclass(frozen=True)
class AIReplyContract:
    runtime_trace_id: str
    contract_version: str
    runtime_signature: str
    safety_status: str
    reply_type: str | None = None
    used_sources: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    confidence: float | None = None
    channel: str | None = None

    def payload_dict(self, *, body: str | None, origin: str = "provider_runtime", customer_visible: bool = True) -> dict[str, Any]:
        return build_ai_reply_contract_payload(
            body=body,
            runtime_trace_id=self.runtime_trace_id,
            contract_version=self.contract_version,
            runtime_signature=self.runtime_signature,
            safety_status=self.safety_status,
            origin=origin,
            reply_type=self.reply_type or "answer",
            used_sources=list(self.used_sources),
            unsupported_claims=list(self.unsupported_claims),
            conflicts=list(self.conflicts),
            confidence=self.confidence,
            channel=self.channel,
            customer_visible=customer_visible,
        )

    def payload_json(self, *, body: str | None, origin: str = "provider_runtime", customer_visible: bool = True) -> str:
        return canonical_contract_payload_json(self.payload_dict(body=body, origin=origin, customer_visible=customer_visible))

    def payload_sha256(self, *, body: str | None, origin: str = "provider_runtime", customer_visible: bool = True) -> str | None:
        return contract_payload_sha256(self.payload_json(body=body, origin=origin, customer_visible=customer_visible))


def build_ai_reply_contract(
    *,
    body: str | None,
    runtime_trace: dict[str, Any] | None,
    safety_status: str = "passed",
    contract_version: str = AI_REPLY_CONTRACT_V2,
    reply_type: str = "answer",
    used_sources: list[str] | tuple[str, ...] | None = None,
    unsupported_claims: list[str] | tuple[str, ...] | None = None,
    conflicts: list[str] | tuple[str, ...] | None = None,
    confidence: float | None = None,
    channel: str | None = None,
) -> AIReplyContract:
    trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    trace_id = _trace_id(trace)
    v3_violation = validate_ai_reply_v3_payload(
        contract_version=contract_version,
        reply_type=reply_type,
        used_sources=used_sources,
        unsupported_claims=unsupported_claims,
        customer_visible=reply_type != "null_reply",
    )
    if v3_violation:
        raise ValueError(v3_violation)
    return AIReplyContract(
        runtime_trace_id=trace_id,
        contract_version=contract_version,
        runtime_signature=sign_ai_reply_contract(
            body=body,
            runtime_trace_id=trace_id,
            contract_version=contract_version,
            safety_status=safety_status,
            reply_type=reply_type,
            used_sources=used_sources,
            unsupported_claims=unsupported_claims,
            conflicts=conflicts,
            confidence=confidence,
            channel=channel,
        ),
        safety_status=safety_status,
        reply_type=reply_type if contract_version == AI_REPLY_CONTRACT_V3 else None,
        used_sources=tuple(_clean_list(used_sources)),
        unsupported_claims=tuple(_clean_list(unsupported_claims)),
        conflicts=tuple(_clean_list(conflicts)),
        confidence=confidence,
        channel=channel,
    )


def sign_ai_reply_contract(
    *,
    body: str | None,
    runtime_trace_id: str,
    contract_version: str,
    safety_status: str,
    reply_type: str = "answer",
    used_sources: list[str] | tuple[str, ...] | None = None,
    unsupported_claims: list[str] | tuple[str, ...] | None = None,
    conflicts: list[str] | tuple[str, ...] | None = None,
    confidence: float | None = None,
    channel: str | None = None,
) -> str:
    payload = {
        "body_sha256": _body_sha256(body, reply_type=reply_type),
        "runtime_trace_id": runtime_trace_id,
        "contract_version": contract_version,
        "safety_status": safety_status,
    }
    if contract_version == AI_REPLY_CONTRACT_V3:
        payload.update(
            {
                "reply": {"type": reply_type, "text_sha256": payload["body_sha256"]},
                "grounding": {
                    "used_sources": _clean_list(used_sources),
                    "unsupported_claims": _clean_list(unsupported_claims),
                    "conflicts": _clean_list(conflicts),
                },
                "risk": {"confidence": confidence},
                "channel": channel,
            }
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    secret = runtime_contract_signing_secret()
    return hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_ai_reply_contract(
    *,
    body: str | None,
    runtime_trace_id: str | None,
    contract_version: str | None,
    runtime_signature: str | None,
    safety_status: str | None,
    reply_type: str = "answer",
    used_sources: list[str] | tuple[str, ...] | None = None,
    unsupported_claims: list[str] | tuple[str, ...] | None = None,
    conflicts: list[str] | tuple[str, ...] | None = None,
    confidence: float | None = None,
    channel: str | None = None,
    customer_visible: bool = True,
) -> str | None:
    if not runtime_trace_id:
        return "runtime_trace_id_required"
    if contract_version not in VALID_AI_REPLY_CONTRACTS:
        return "runtime_contract_version_invalid"
    if safety_status not in VALID_SAFETY_STATUSES:
        return "runtime_safety_status_invalid"
    v3_violation = validate_ai_reply_v3_payload(
        contract_version=contract_version,
        reply_type=reply_type,
        used_sources=used_sources,
        unsupported_claims=unsupported_claims,
        customer_visible=customer_visible,
    )
    if v3_violation:
        return v3_violation
    expected = sign_ai_reply_contract(
        body=body,
        runtime_trace_id=runtime_trace_id,
        contract_version=contract_version,
        safety_status=safety_status,
        reply_type=reply_type,
        used_sources=used_sources,
        unsupported_claims=unsupported_claims,
        conflicts=conflicts,
        confidence=confidence,
        channel=channel,
    )
    if runtime_signature != expected:
        return "runtime_signature_invalid"
    return None


def validate_ai_reply_v3_payload(
    *,
    contract_version: str | None,
    reply_type: str = "answer",
    used_sources: list[str] | tuple[str, ...] | None = None,
    unsupported_claims: list[str] | tuple[str, ...] | None = None,
    customer_visible: bool = True,
) -> str | None:
    if contract_version != AI_REPLY_CONTRACT_V3:
        return None
    if reply_type not in VALID_REPLY_TYPES:
        return "ai_reply_v3_reply_type_invalid"
    if reply_type == "null_reply":
        if customer_visible:
            return "ai_reply_v3_null_reply_not_customer_visible"
        return None
    if reply_type == "answer" and not _clean_list(used_sources):
        return "ai_reply_v3_answer_requires_used_sources"
    if reply_type == "answer" and _clean_list(unsupported_claims):
        return "ai_reply_v3_unsupported_claims_blocked"
    if reply_type == "handoff_notice" and _clean_list(unsupported_claims):
        return "ai_reply_v3_handoff_notice_unsupported_claims_blocked"
    return None


def runtime_contract_signing_secret() -> str:
    settings = get_settings()
    secret = settings.runtime_contract_signing_secret
    if settings.app_env in {"test", "development", "local"} and not secret:
        return "test-runtime-contract-signing-secret"
    if runtime_contract_secret_problem(secret):
        raise RuntimeError("RUNTIME_CONTRACT_SIGNING_SECRET must be a strong secret")
    return secret


def runtime_contract_secret_problem(secret: str | None) -> str | None:
    value = (secret or "").strip()
    if len(value) < 32:
        return "too_short"
    if value.lower() in WEAK_RUNTIME_CONTRACT_SECRETS:
        return "placeholder"
    if value.startswith("dev-only-"):
        return "placeholder"
    return None


def runtime_contract_secret_ready() -> dict[str, Any]:
    settings = get_settings()
    problem = runtime_contract_secret_problem(settings.runtime_contract_signing_secret)
    return {
        "ok": problem is None or settings.app_env in {"test", "development", "local"},
        "configured": bool(settings.runtime_contract_signing_secret),
        "problem": None if problem is None else problem,
    }


def build_ai_reply_contract_payload(
    *,
    body: str | None,
    runtime_trace_id: str,
    contract_version: str,
    runtime_signature: str,
    safety_status: str,
    origin: str,
    reply_type: str = "answer",
    used_sources: list[str] | tuple[str, ...] | None = None,
    unsupported_claims: list[str] | tuple[str, ...] | None = None,
    conflicts: list[str] | tuple[str, ...] | None = None,
    confidence: float | None = None,
    channel: str | None = None,
    customer_visible: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "runtime_trace_id": runtime_trace_id,
        "contract_version": contract_version,
        "runtime_signature": runtime_signature,
        "safety_status": safety_status,
        "origin": origin,
    }
    if contract_version == AI_REPLY_CONTRACT_V3:
        payload.update(
            {
                "reply": {
                    "type": reply_type,
                    "text": None if reply_type == "null_reply" else body,
                },
                "customer_visible": bool(customer_visible),
                "grounding": {
                    "used_sources": _clean_list(used_sources),
                    "unsupported_claims": _clean_list(unsupported_claims),
                    "conflicts": _clean_list(conflicts),
                },
                "risk": {"confidence": confidence},
                "channel": channel,
            }
        )
    return payload


def canonical_contract_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def contract_payload_sha256(payload_json: str | None) -> str | None:
    if not payload_json:
        return None
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def validate_contract_payload_hash(payload_json: str | None, expected_sha256: str | None) -> str | None:
    if not payload_json and not expected_sha256:
        return None
    if not payload_json or not expected_sha256:
        return "runtime_contract_payload_hash_missing"
    if contract_payload_sha256(payload_json) != expected_sha256:
        return "runtime_contract_payload_hash_invalid"
    return None


def parse_runtime_contract_payload(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def contract_validation_args_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    reply = payload.get("reply") if isinstance(payload.get("reply"), dict) else {}
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    risk = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
    return {
        "runtime_trace_id": payload.get("runtime_trace_id"),
        "contract_version": payload.get("contract_version"),
        "runtime_signature": payload.get("runtime_signature"),
        "safety_status": payload.get("safety_status"),
        "reply_type": reply.get("type") or "answer",
        "used_sources": grounding.get("used_sources"),
        "unsupported_claims": grounding.get("unsupported_claims"),
        "conflicts": grounding.get("conflicts"),
        "confidence": risk.get("confidence"),
        "channel": payload.get("channel"),
        "customer_visible": bool(payload.get("customer_visible", True)),
    }


def _trace_id(trace: dict[str, Any]) -> str:
    for key in ("runtime_trace_id", "trace_id", "request_id"):
        value = trace.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    encoded = json.dumps(trace, ensure_ascii=False, sort_keys=True, default=str)
    if encoded and encoded != "{}":
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"rt_{uuid.uuid4().hex}"


def _body_sha256(body: str | None, *, reply_type: str) -> str | None:
    if reply_type == "null_reply" and body is None:
        return None
    return hashlib.sha256((body or "").encode("utf-8", errors="ignore")).hexdigest()


def _clean_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item:
            cleaned.append(item[:240])
    return cleaned
