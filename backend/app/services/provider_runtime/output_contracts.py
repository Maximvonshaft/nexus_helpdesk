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
_MAX_FAST_REPLY_CHARS = 1200
_MAX_VISIBLE_PREFIX_CHARS = 80
_NEUTRAL_IDENTITY_ZH = "我是在线客服助手，可以协助处理常见客户服务问题。"
_NEUTRAL_IDENTITY_EN = "I’m an online support assistant. I can help with common customer service questions."
_CHINESE_IDENTITY_EXACT = {
    "你是谁",
    "你是什么客服",
    "你是哪里的客服",
    "你是哪家客服",
    "什么客服",
    "哪里的客服",
    "哪家客服",
}
_ENGLISH_IDENTITY_EXACT = {
    "who are you",
    "what support are you",
    "which company support are you",
}
_IDENTITY_FIELDS = {
    "brand_name",
    "assistant_name",
    "role_label",
    "identity_statement",
    "identity_answer_rule",
    "capabilities",
    "disallowed_identity_claims",
    "handoff_boundary",
    "tone",
    "guardrails",
}


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
            parsed = OutputContracts.enforce_identity_fast_reply(parsed, persona_context, request_body)
            parsed = OutputContracts.enforce_persona_fast_reply(parsed, persona_context)
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
    def enforce_persona_fast_reply(parsed: dict[str, Any], persona_context: dict[str, Any] | None) -> dict[str, Any]:
        prefix = OutputContracts.extract_persona_visible_prefix(persona_context)
        if not prefix:
            return parsed
        reply = str(parsed.get("customer_reply") or "").strip()
        if not reply or reply.startswith(prefix):
            return {**parsed, "customer_reply": reply}
        return {**parsed, "customer_reply": OutputContracts._truncate_reply(f"{prefix} {reply}")}

    @staticmethod
    def enforce_identity_fast_reply(
        parsed: dict[str, Any],
        persona_context: dict[str, Any] | None,
        request_body: Any,
    ) -> dict[str, Any]:
        if not OutputContracts.is_identity_question(request_body):
            return parsed
        identity = OutputContracts.extract_identity_context(persona_context)
        reply = OutputContracts.build_identity_reply(identity, request_body)
        if not reply:
            return parsed
        return {
            **parsed,
            "customer_reply": OutputContracts._truncate_reply(reply),
            "intent": "other",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
            "ticket_should_create": False,
        }

    @staticmethod
    def is_identity_question(request_body: Any) -> bool:
        if not isinstance(request_body, str):
            return False
        text = request_body.strip()
        if not text:
            return False
        chinese = re.sub(r"[\s，。？！,.!?;:：；、]+", "", text)
        if chinese in _CHINESE_IDENTITY_EXACT:
            return True
        if re.fullmatch(r"你(?:是否是|是不是|是).{1,40}(?:的)?客服", chinese):
            return True
        english = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        english = " ".join(english.split())
        if english in _ENGLISH_IDENTITY_EXACT:
            return True
        return bool(re.fullmatch(r"are you .{1,80} support", english))

    @staticmethod
    def extract_identity_context(persona_context: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(persona_context, dict):
            return {}
        content = persona_context.get("content_json")
        if not isinstance(content, dict):
            content = {}
        nested_content = content.get("identity_context")
        identity = persona_context.get("identity_context")
        merged: dict[str, Any] = {}
        if isinstance(nested_content, dict):
            merged.update(nested_content)
        merged.update({key: content[key] for key in content if key in _IDENTITY_FIELDS and content[key] not in (None, "", [])})
        if isinstance(identity, dict):
            merged.update({key: value for key, value in identity.items() if key in _IDENTITY_FIELDS and value not in (None, "", [])})
        return {
            "brand_name": OutputContracts._identity_string(merged.get("brand_name")),
            "assistant_name": OutputContracts._identity_string(merged.get("assistant_name")),
            "role_label": OutputContracts._identity_string(merged.get("role_label")),
            "identity_statement": OutputContracts._identity_string(merged.get("identity_statement")),
            "identity_answer_rule": OutputContracts._identity_string(merged.get("identity_answer_rule")),
            "capabilities": OutputContracts._identity_list(merged.get("capabilities")),
            "disallowed_identity_claims": OutputContracts._identity_list(merged.get("disallowed_identity_claims")),
            "handoff_boundary": OutputContracts._identity_string(merged.get("handoff_boundary")),
            "tone": OutputContracts._identity_string(merged.get("tone")),
            "guardrails": OutputContracts._identity_list(merged.get("guardrails")),
        }

    @staticmethod
    def build_identity_reply(identity: dict[str, Any], request_body: Any) -> str | None:
        chinese = OutputContracts._contains_cjk(str(request_body or ""))
        brand = OutputContracts._identity_string(identity.get("brand_name"))
        assistant = OutputContracts._identity_string(identity.get("assistant_name"))
        role = OutputContracts._identity_string(identity.get("role_label")) or ("AI 客服" if chinese else "AI support")
        statement = OutputContracts._identity_string(identity.get("identity_statement"))
        capabilities = OutputContracts._identity_list(identity.get("capabilities"))
        disallowed = OutputContracts._identity_list(identity.get("disallowed_identity_claims"))
        nexus_allowed = OutputContracts._is_explicit_nexusdesk(brand) or OutputContracts._is_explicit_nexusdesk(assistant)

        candidates: list[str] = []
        if statement:
            candidates.append(statement)
        if assistant:
            capability_text = OutputContracts._capability_text(capabilities, chinese)
            if brand:
                base = f"我是{assistant}，{brand}的{role}" if chinese else f"I’m {assistant}, {brand} {role}"
            else:
                base = f"我是{assistant}" if chinese else f"I’m {assistant}"
            if capability_text:
                candidates.append(
                    f"{base}，可以协助{capability_text}。"
                    if chinese
                    else f"{base}. I can help with {capability_text}."
                )
            else:
                candidates.append(f"{base}。" if chinese else f"{base}.")
        if brand:
            candidates.append(f"我是{brand} AI 客服。" if chinese else f"I’m {brand} AI support.")
        candidates.append(_NEUTRAL_IDENTITY_ZH if chinese else _NEUTRAL_IDENTITY_EN)

        for candidate in candidates:
            cleaned = OutputContracts._truncate_reply(candidate)
            if OutputContracts._contains_forbidden_identity_claim(cleaned, disallowed, nexus_allowed=nexus_allowed):
                continue
            return cleaned
        return None

    @staticmethod
    def _identity_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @staticmethod
    def _identity_list(value: Any) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = [value]
        else:
            return []
        items: list[str] = []
        for item in raw_items:
            if not isinstance(item, str):
                continue
            cleaned = " ".join(item.strip().split())
            if cleaned:
                items.append(cleaned)
        return items

    @staticmethod
    def _capability_text(capabilities: list[str], chinese: bool) -> str:
        if not capabilities:
            return "常见客户服务问题" if chinese else "common customer service questions"
        separator = "、" if chinese else ", "
        return separator.join(capabilities[:4])

    @staticmethod
    def _contains_cjk(value: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in value)

    @staticmethod
    def _is_explicit_nexusdesk(value: str | None) -> bool:
        return bool(value and value.strip().lower() == "nexusdesk")

    @staticmethod
    def _contains_forbidden_identity_claim(text: str, disallowed: list[str], *, nexus_allowed: bool) -> bool:
        lower = text.lower()
        if not nexus_allowed and "nexusdesk" in lower:
            return True
        for claim in disallowed:
            if claim and claim.lower() in lower:
                return True
        return False

    @staticmethod
    def extract_persona_visible_prefix(persona_context: dict[str, Any] | None) -> str | None:
        if not isinstance(persona_context, dict):
            return None
        content_json = persona_context.get("content_json")
        if not isinstance(content_json, dict):
            return None
        for key in ("must_prefix", "reply_prefix", "visible_prefix"):
            value = content_json.get(key)
            if not isinstance(value, str):
                continue
            cleaned = " ".join(value.strip().split())
            if not cleaned or len(cleaned) > _MAX_VISIBLE_PREFIX_CHARS:
                continue
            if "[REDACTED_" in cleaned:
                continue
            if any(marker in cleaned.lower() for marker in _INTERNAL_PATTERNS):
                continue
            if any(pattern.search(cleaned) for pattern in _SECRET_PATTERNS):
                continue
            return cleaned
        return None

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
