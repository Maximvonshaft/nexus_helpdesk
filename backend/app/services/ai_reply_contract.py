from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..enums import ConversationState

AI_REPLY_CONTRACT_V2 = "nexus.ai_reply.v2"

AI_ORIGINS = {"provider_runtime", "ai_runtime"}
HUMAN_ORIGIN = "human_agent"
FORBIDDEN_CUSTOMER_VISIBLE_ORIGINS = {"business_system", "tool_service", "knowledge_runtime", "safety_service"}
VALID_SAFETY_STATUSES = {"passed", "reviewed"}
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


def build_ai_reply_contract(
    *,
    body: str,
    runtime_trace: dict[str, Any] | None,
    safety_status: str = "passed",
    contract_version: str = AI_REPLY_CONTRACT_V2,
) -> AIReplyContract:
    trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    trace_id = _trace_id(trace)
    return AIReplyContract(
        runtime_trace_id=trace_id,
        contract_version=contract_version,
        runtime_signature=sign_ai_reply_contract(
            body=body,
            runtime_trace_id=trace_id,
            contract_version=contract_version,
            safety_status=safety_status,
        ),
        safety_status=safety_status,
    )


def sign_ai_reply_contract(
    *,
    body: str,
    runtime_trace_id: str,
    contract_version: str,
    safety_status: str,
) -> str:
    payload = {
        "body_sha256": hashlib.sha256((body or "").encode("utf-8", errors="ignore")).hexdigest(),
        "runtime_trace_id": runtime_trace_id,
        "contract_version": contract_version,
        "safety_status": safety_status,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_ai_reply_contract(
    *,
    body: str,
    runtime_trace_id: str | None,
    contract_version: str | None,
    runtime_signature: str | None,
    safety_status: str | None,
) -> str | None:
    if not runtime_trace_id:
        return "runtime_trace_id_required"
    if contract_version != AI_REPLY_CONTRACT_V2:
        return "runtime_contract_version_invalid"
    if safety_status not in VALID_SAFETY_STATUSES:
        return "runtime_safety_status_invalid"
    expected = sign_ai_reply_contract(
        body=body,
        runtime_trace_id=runtime_trace_id,
        contract_version=contract_version,
        safety_status=safety_status,
    )
    if runtime_signature != expected:
        return "runtime_signature_invalid"
    return None


def _trace_id(trace: dict[str, Any]) -> str:
    for key in ("runtime_trace_id", "trace_id", "request_id"):
        value = trace.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    encoded = json.dumps(trace, ensure_ascii=False, sort_keys=True, default=str)
    if encoded and encoded != "{}":
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"rt_{uuid.uuid4().hex}"
