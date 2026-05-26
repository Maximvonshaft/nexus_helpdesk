from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

_SECRET_PATTERNS = [
    re.compile(("s" + "k-") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("ey" + "J") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("Bear" + "er") + r"\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
]
_INTERNAL_PATTERNS = ["localhost", "127.0.0.1", "::1", "bridge", "codex_app_server", "openclaw", "provider_runtime"]
_STATUS_WORDS = [
    "delivered", "in transit", "out for delivery", "customs", "returned", "failed delivery",
    "派送", "已签收", "运输中", "清关", "退回",
]
_IDENTITY_NEGATION_PATTERNS = ["不是", "不能代表", "无法代表", "not ", "cannot represent", "can't represent"]
_IDENTITY_EN_RE = re.compile(
    r"\b(?:who\s+are\s+you|what\s+(?:kind\s+of\s+)?(?:support|customer\s+service)\s+are\s+you|"
    r"are\s+you\s+(?:the\s+)?(?:.*\s+)?(?:support|customer\s+service)|"
    r"which\s+(?:company|brand|store)\s+(?:support|customer\s+service)\s+are\s+you)\b",
    re.IGNORECASE,
)
_MAX_FAST_REPLY_CHARS = 1200
_MAX_VISIBLE_PREFIX_CHARS = 80


class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict[str, Any]:
        if contract_name == "speedaf_webchat_fast_reply_v1":
            return {
                "type": "object",
                "properties": {
                    "customer_reply": {"type": "string", "maxLength": 1200},
                    "language": {"type": "string", "maxLength": 32},
                    "intent": {"type": "string", "enum": ["greeting", "tracking", "tracking_missing_number", "tracking_unresolved", "complaint", "address_change", "handoff", "other"]},
                    "tracking_number": {"type": ["string", "null"], "maxLength": 80},
                    "handoff_required": {"type": "boolean"},
                    "handoff_reason": {"type": ["string", "null"], "maxLength": 500},
                    "recommended_agent_action": {"type": ["string", "null"], "maxLength": 500},
                    "ticket_should_create": {"type": "boolean"},
                    "internal_summary": {"type": ["string", "null"], "maxLength": 1000},
                    "risk_flags": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                },
                "required": ["customer_reply", "language", "intent", "handoff_required", "ticket_should_create"],
                "additionalProperties": False,
            }
        if contract_name == "speedaf_ticket_triage_v1":
            return {
                "type": "object",
                "properties": {
                    "ticket_title": {"type": "string", "maxLength": 200},
                    "ticket_category": {"type": "string", "enum": ["delivery_exception", "tracking", "complaint", "address_change", "claim", "other"]},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "customer_reply": {"type": "string", "maxLength": 1200},
                    "agent_brief": {"type": "string", "maxLength": 2000},
                    "required_human_action": {"type": ["string", "null"], "maxLength": 1000},
                    "evidence_needed": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                    "handoff_required": {"type": "boolean"},
                },
                "required": ["ticket_title", "ticket_category", "priority", "customer_reply", "agent_brief", "handoff_required", "evidence_needed"],
                "additionalProperties": False,
            }
        if contract_name == "speedaf_delivery_exception_analysis_v1":
            return {
                "type": "object",
                "properties": {
                    "exception_type": {"type": "string", "enum": ["failed_delivery", "delivered_not_received", "wrong_address", "customs", "damaged", "lost", "other"]},
                    "root_cause_guess": {"type": ["string", "null"], "maxLength": 1000},
                    "next_action": {"type": "string", "enum": ["reattempt", "investigate", "return", "manual_review", "none"]},
                    "customer_visible_reply": {"type": "string", "maxLength": 1200},
                    "internal_action_required": {"type": "boolean"},
                    "evidence_needed": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                },
                "required": ["exception_type", "next_action", "customer_visible_reply", "internal_action_required", "evidence_needed"],
                "additionalProperties": False,
            }
        return {}

    @staticmethod
    def validate_and_parse(
        contract_name: str,
        raw_output: str,
        evidence_present: bool = False,
        persona_context: dict[str, Any] | None = None,
        request_body: Any = None,
    ) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError("Output must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Output must be a JSON object")
        if contract_name == "speedaf_webchat_fast_reply_v1":
            parsed = OutputContracts._normalize_fast_reply_v1(parsed)
            parsed = OutputContracts.enforce_persona_fast_reply(parsed, persona_context, request_body=request_body)
            raw_output = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))

        schema = OutputContracts.get_schema(contract_name)
        if schema:
            try:
                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.exceptions.ValidationError as exc:
                raise ValueError(f"Schema validation failed: {exc.message}") from exc

        OutputContracts.check_security_rules(raw_output=raw_output, parsed=parsed, evidence_present=evidence_present)
        return parsed

    @staticmethod
    def _normalize_fast_reply_v1(parsed: dict[str, Any]) -> dict[str, Any]:
        if "customer_reply" in parsed or not isinstance(parsed.get("reply"), str):
            return parsed
        parsed = {**parsed, "customer_reply": parsed["reply"]}
        parsed.pop("reply", None)
        parsed.setdefault("language", "en")
        parsed.setdefault("ticket_should_create", False)
        parsed.setdefault("handoff_required", False)
        parsed.setdefault("tracking_number", None)
        parsed.setdefault("handoff_reason", None)
        parsed.setdefault("recommended_agent_action", None)
        parsed.setdefault("internal_summary", None)
        parsed.setdefault("risk_flags", [])
        return parsed

    @staticmethod
    def enforce_persona_fast_reply(parsed: dict[str, Any], persona_context: dict[str, Any] | None, *, request_body: Any = None) -> dict[str, Any]:
        identity_reply = OutputContracts.extract_persona_identity_reply(persona_context, request_body)
        if identity_reply:
            return {**parsed, "customer_reply": OutputContracts._truncate_reply(identity_reply), "intent": "greeting"}

        prefix = OutputContracts.extract_persona_visible_prefix(persona_context)
        if not prefix:
            return parsed
        reply = str(parsed.get("customer_reply") or "").strip()
        if not reply or reply.startswith(prefix):
            return {**parsed, "customer_reply": reply}
        return {**parsed, "customer_reply": OutputContracts._truncate_reply(f"{prefix} {reply}")}

    @staticmethod
    def extract_persona_identity_reply(persona_context: dict[str, Any] | None, request_body: Any = None) -> str | None:
        if not OutputContracts._looks_like_identity_question(request_body):
            return None
        if not isinstance(persona_context, dict):
            return None
        content_json = persona_context.get("content_json")
        if not isinstance(content_json, dict):
            return None

        identity = OutputContracts._safe_text(content_json.get("identity"))
        brand_name = OutputContracts._safe_text(content_json.get("brand_name"))
        answer_rule = OutputContracts._safe_text(content_json.get("identity_answer_rule"))
        capabilities = content_json.get("capabilities")
        capability_text = OutputContracts._capability_text(capabilities)

        if identity:
            if capability_text:
                return f"您好，{identity}我可以协助处理{capability_text}。"
            return f"您好，{identity}请问有什么可以帮您？"
        if brand_name:
            if capability_text:
                return f"您好，我是{brand_name}的 AI 客服，可以协助处理{capability_text}。"
            return f"您好，我是{brand_name}的 AI 客服，请问有什么可以帮您？"
        if answer_rule:
            # Fallback for deployments that only provide a rule sentence.
            marker = "必须明确回答："
            if marker in answer_rule:
                answer = answer_rule.split(marker, 1)[1].strip()
                return answer.split("。", 1)[0].strip(" ：:；;，,") + "。"
            return answer_rule
        return None

    @staticmethod
    def _looks_like_identity_question(request_body: Any) -> bool:
        text = " ".join(str(request_body or "").strip().lower().split())
        if not text:
            return False
        compact = re.sub(r"\s+", "", text)
        if any(token in compact for token in ("你是谁", "你是什么客服", "你是哪里的客服", "你是哪家客服", "你是否是", "你是不是", "你属于哪里", "什么客服", "哪里的客服", "哪家客服")):
            return True
        if "你" in compact and "客服" in compact and any(token in compact for token in ("是", "哪", "什么", "谁")):
            return True
        return _IDENTITY_EN_RE.search(text) is not None

    @staticmethod
    def extract_persona_visible_prefix(persona_context: dict[str, Any] | None) -> str | None:
        if not isinstance(persona_context, dict):
            return None
        content_json = persona_context.get("content_json")
        if not isinstance(content_json, dict):
            return None
        for key in ("must_prefix", "reply_prefix", "visible_prefix"):
            value = content_json.get(key)
            cleaned = OutputContracts._safe_text(value)
            if not cleaned or len(cleaned) > _MAX_VISIBLE_PREFIX_CHARS:
                continue
            return cleaned
        return None

    @staticmethod
    def _safe_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if "[redacted_" in lowered or "[redacted" in lowered:
            return None
        if any(marker in lowered for marker in _INTERNAL_PATTERNS):
            return None
        if any(pattern.search(cleaned) for pattern in _SECRET_PATTERNS):
            return None
        return cleaned

    @staticmethod
    def _capability_text(value: Any) -> str | None:
        if not isinstance(value, list):
            return None
        items = [OutputContracts._safe_text(item) for item in value]
        items = [item for item in items if item]
        if not items:
            return None
        return "、".join(items[:5])

    @staticmethod
    def _truncate_reply(value: str) -> str:
        cleaned = " ".join(str(value or "").strip().split())
        if len(cleaned) <= _MAX_FAST_REPLY_CHARS:
            return cleaned
        return cleaned[: _MAX_FAST_REPLY_CHARS - 3].rstrip() + "..."

    @staticmethod
    def check_security_rules(*, raw_output: str, parsed: dict[str, Any], evidence_present: bool = False) -> None:
        lower_raw = raw_output.lower()
        if "```" in raw_output or "~~~" in raw_output:
            raise ValueError("Markdown code blocks are prohibited")
        if "<think" in lower_raw or "hidden reasoning" in lower_raw:
            raise ValueError("Hidden reasoning is prohibited")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(raw_output):
                raise ValueError("Potential secret leakage detected")
        for marker in _INTERNAL_PATTERNS:
            if marker in lower_raw:
                raise ValueError("Internal runtime references are prohibited")

        intent = parsed.get("intent")
        reply = str(parsed.get("customer_reply") or parsed.get("customer_visible_reply") or "")
        reply_lower = reply.lower()
        if intent == "tracking":
            if not parsed.get("tracking_number"):
                raise ValueError("Tracking intent without a tracking_number is prohibited")
            if not evidence_present:
                raise ValueError("Tracking status output requires trusted tracking evidence")
        if not evidence_present and any(word in reply_lower for word in _STATUS_WORDS):
            raise ValueError("Parcel status language requires trusted tracking evidence")