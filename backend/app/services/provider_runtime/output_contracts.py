from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
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
_NUMERIC_FACT_UNIT_MARKERS = (
    "day", "days", "business day", "business days", "天", "工作日",
    "chf", "usd", "eur", "fee", "费用", "服务费", "时效", "sla",
    "清关", "customs", "海运", "空运", "shipping", "delivery",
)
_COUNTRY_ALIASES = {
    "switzerland": ("switzerland", "swiss", "瑞士"),
    "nigeria": ("nigeria", "nigerian", "尼日利亚"),
    "united_kingdom": ("united kingdom", "uk", "britain", "英国"),
    "china": ("china", "chinese", "中国"),
}
_SERVICE_ALIASES = {
    "ocean": ("海运", "ocean freight", "ocean shipping", "sea freight", "sea shipping"),
    "air": ("空运", "air freight", "air shipping"),
    "customs": ("清关", "customs clearance", "customs"),
    "address_change": ("地址变更", "address change", "address-change"),
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
        knowledge_context: dict[str, Any] | None = None,
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

        OutputContracts.check_security_rules(
            raw_output=raw_output,
            parsed=parsed,
            evidence_present=evidence_present,
            request_body=request_body,
            knowledge_context=knowledge_context,
        )
        if contract_name == "speedaf_webchat_fast_reply_v1":
            OutputContracts.enforce_locked_facts_fast_reply(
                parsed=parsed,
                request_body=request_body,
                knowledge_context=knowledge_context,
                evidence_present=evidence_present,
            )
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
    def enforce_locked_facts_fast_reply(
        *,
        parsed: dict[str, Any],
        request_body: Any = None,
        knowledge_context: dict[str, Any] | None = None,
        evidence_present: bool = False,
    ) -> None:
        locked_facts = OutputContracts._locked_facts_from_context(
            knowledge_context=knowledge_context,
            request_body=request_body,
            evidence_present=evidence_present,
        )
        if not locked_facts:
            return
        if evidence_present and (parsed.get("intent") == "tracking" or parsed.get("tracking_number")):
            return

        reply = str(parsed.get("customer_reply") or "").strip()
        if not reply:
            raise ValueError("Locked facts require a provider-generated customer_reply")
        if parsed.get("handoff_required") is True:
            raise ValueError("Locked facts require a provider-generated answer")

        conflict_reasons: list[str] = []
        for fact in locked_facts:
            answer = str(fact.get("answer") or fact.get("direct_answer") or "").strip()
            if not answer:
                continue
            conflict_reason = OutputContracts._locked_fact_conflict_reason(reply=reply, answer=answer)
            if conflict_reason:
                conflict_reasons.append(conflict_reason)
                continue
            if OutputContracts._reply_matches_direct_answer(reply, answer):
                return
            conflict_reasons.append("Locked fact missing from provider output")

        if conflict_reasons:
            raise ValueError(conflict_reasons[0])
        raise ValueError("Locked fact missing from provider output")

    @staticmethod
    def _locked_facts_from_context(
        *,
        knowledge_context: dict[str, Any] | None,
        request_body: Any = None,
        evidence_present: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(knowledge_context, dict):
            return []
        facts: list[dict[str, Any]] = []
        for raw in knowledge_context.get("locked_facts") or []:
            if not isinstance(raw, dict):
                continue
            answer = str(raw.get("answer") or raw.get("direct_answer") or "").strip()
            if answer:
                facts.append({**raw, "answer": answer})
        if facts:
            return facts

        hits = knowledge_context.get("hits")
        if not isinstance(hits, list):
            return []
        try:
            from ..knowledge_grounding_service import select_grounding_candidate

            candidate = select_grounding_candidate(
                query=str(request_body or ""),
                hits=hits,
                tracking_fact_evidence_present=evidence_present,
            )
        except Exception:
            return []
        if not candidate:
            return []
        answer = str(candidate.get("answer") or "").strip()
        if not answer:
            return []
        return [{"answer": answer, "source": candidate.get("source") or {}, "mode": "locked_fact"}]

    @staticmethod
    def _locked_fact_conflict_reason(*, reply: str, answer: str) -> str | None:
        answer_numbers = OutputContracts._number_terms(answer)
        reply_numbers = OutputContracts._number_terms(reply)
        if answer_numbers:
            if not answer_numbers.issubset(reply_numbers):
                return "Locked fact numeric conflict"
            extra_numbers = reply_numbers - answer_numbers
            if extra_numbers and OutputContracts._contains_numeric_fact_unit(f"{answer} {reply}"):
                return "Locked fact numeric conflict"

        answer_countries = OutputContracts._country_terms(answer)
        reply_countries = OutputContracts._country_terms(reply)
        if answer_countries and reply_countries and not reply_countries.issubset(answer_countries):
            return "Locked fact entity conflict"

        answer_services = OutputContracts._service_terms(answer)
        reply_services = OutputContracts._service_terms(reply)
        if answer_services and reply_services and not reply_services.issubset(answer_services):
            return "Locked fact service conflict"

        answer_pairs = OutputContracts._service_number_pairs(answer)
        reply_pairs = OutputContracts._service_number_pairs(reply)
        if answer_pairs and reply_pairs:
            answer_pair_services = {service for service, _number in answer_pairs}
            conflicting_pairs = {
                pair for pair in reply_pairs
                if pair[0] in answer_pair_services and pair not in answer_pairs
            }
            if conflicting_pairs:
                return "Locked fact service conflict"
        return None

    @staticmethod
    def _number_terms(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", value or "")
        terms: set[str] = set()
        for match in re.finditer(r"(?<![A-Z0-9])\d+(?:\.\d+)?(?![A-Z0-9])", normalized, flags=re.I):
            try:
                decimal = Decimal(match.group(0))
            except InvalidOperation:
                continue
            terms.add(str(decimal.normalize()).lower())
        return terms

    @staticmethod
    def _contains_numeric_fact_unit(value: str) -> bool:
        normalized = unicodedata.normalize("NFKC", value or "").lower()
        return any(marker in normalized for marker in _NUMERIC_FACT_UNIT_MARKERS)

    @staticmethod
    def _country_terms(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", value or "").lower()
        found: set[str] = set()
        for key, aliases in _COUNTRY_ALIASES.items():
            for alias in aliases:
                if re.search(OutputContracts._alias_pattern(alias), normalized):
                    found.add(key)
                    break
        return found

    @staticmethod
    def _service_terms(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", value or "").lower()
        found: set[str] = set()
        for key, aliases in _SERVICE_ALIASES.items():
            for alias in aliases:
                if re.search(OutputContracts._alias_pattern(alias), normalized):
                    found.add(key)
                    break
        return found

    @staticmethod
    def _service_number_pairs(value: str) -> set[tuple[str, str]]:
        normalized = unicodedata.normalize("NFKC", value or "").lower()
        numbers = [
            (match.start(), match.end(), next(iter(OutputContracts._number_terms(match.group(0))), match.group(0)))
            for match in re.finditer(r"(?<![A-Z0-9])\d+(?:\.\d+)?(?![A-Z0-9])", normalized, flags=re.I)
        ]
        mentions: list[tuple[int, int, str]] = []
        for service, aliases in _SERVICE_ALIASES.items():
            for alias in aliases:
                for match in re.finditer(OutputContracts._alias_pattern(alias), normalized):
                    mentions.append((match.start(), match.end(), service))
        mentions.sort(key=lambda item: item[0])
        pairs: set[tuple[str, str]] = set()
        for index, (mention_start, mention_end, service) in enumerate(mentions):
            next_service_start = mentions[index + 1][0] if index + 1 < len(mentions) else len(normalized)
            scoped_numbers = [
                (start, end, number)
                for start, end, number in numbers
                if mention_end <= start < next_service_start
            ]
            if not scoped_numbers:
                scoped_numbers = sorted(
                    numbers,
                    key=lambda item: min(abs(mention_end - item[0]), abs(mention_start - item[1])),
                )[:1]
            for start, end, number in scoped_numbers:
                if min(abs(mention_end - start), abs(mention_start - end)) <= 24:
                    pairs.add((service, number))
        return pairs

    @staticmethod
    def _alias_pattern(alias: str) -> str:
        escaped = re.escape(alias.lower())
        if re.fullmatch(r"[a-z0-9][a-z0-9\s-]*", alias.lower()):
            return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
        return escaped

    @staticmethod
    def check_security_rules(
        *,
        raw_output: str,
        parsed: dict[str, Any],
        evidence_present: bool = False,
        request_body: Any = None,
        knowledge_context: dict[str, Any] | None = None,
    ) -> None:
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
        if (
            not evidence_present
            and any(word in reply_lower for word in _STATUS_WORDS)
            and not OutputContracts._is_safe_grounded_business_reply(
                parsed=parsed,
                reply=reply,
                request_body=request_body,
                knowledge_context=knowledge_context,
            )
        ):
            if not OutputContracts._looks_like_specific_parcel_status_claim(reply):
                for fact in OutputContracts._locked_facts_from_context(
                    knowledge_context=knowledge_context,
                    request_body=request_body,
                    evidence_present=False,
                ):
                    answer = str(fact.get("answer") or "").strip()
                    conflict_reason = OutputContracts._locked_fact_conflict_reason(reply=reply, answer=answer)
                    if conflict_reason and OutputContracts._meaningful_overlap_terms(reply, answer):
                        raise ValueError(conflict_reason)
            raise ValueError("Parcel status language requires trusted tracking evidence")

    @staticmethod
    def _is_safe_grounded_business_reply(
        *,
        parsed: dict[str, Any],
        reply: str,
        request_body: Any,
        knowledge_context: dict[str, Any] | None,
    ) -> bool:
        if parsed.get("intent") == "tracking" or parsed.get("tracking_number"):
            return False
        if parsed.get("handoff_required") is True:
            return False
        if OutputContracts._looks_like_specific_parcel_status_claim(reply):
            return False
        if not isinstance(knowledge_context, dict):
            return False
        for fact in OutputContracts._locked_facts_from_context(
            knowledge_context=knowledge_context,
            request_body=request_body,
            evidence_present=False,
        ):
            answer = str(fact.get("answer") or "").strip()
            if answer and not OutputContracts._locked_fact_conflict_reason(reply=reply, answer=answer):
                if OutputContracts._reply_matches_direct_answer(reply, answer):
                    return True
        hits = knowledge_context.get("hits")
        if not isinstance(hits, list):
            return False
        try:
            from ..knowledge_grounding_service import select_grounding_candidate

            candidate = select_grounding_candidate(
                query=str(request_body or ""),
                hits=hits,
                tracking_fact_evidence_present=False,
            )
        except Exception:
            return False
        if not candidate:
            return False
        return OutputContracts._reply_matches_direct_answer(reply, str(candidate.get("answer") or ""))

    @staticmethod
    def _looks_like_specific_parcel_status_claim(reply: str) -> bool:
        text = " ".join(str(reply or "").strip().lower().split())
        if not text:
            return False
        latin_status = r"(?:delivered|in transit|out for delivery|customs|returned|failed delivery)"
        latin_parcel = r"(?:parcel|package|shipment|order|waybill|tracking)"
        if re.search(rf"\b(?:your|this|the)\s+{latin_parcel}\b.{{0,80}}\b{latin_status}\b", text):
            return True
        if re.search(rf"\b{latin_status}\b.{{0,80}}\b(?:your|this|the)\s+{latin_parcel}\b", text):
            return True
        cjk_status = r"(?:派送|派送中|已签收|签收|妥投|运输中|清关|退回|投递失败)"
        cjk_parcel = r"(?:包裹|快递|货件|运单|单号)"
        if re.search(rf"(?:你(?:的)?|您(?:的)?|该|此|这个|这票)?{cjk_parcel}.{{0,40}}{cjk_status}", text):
            return True
        if re.search(rf"{cjk_status}.{{0,40}}(?:你(?:的)?|您(?:的)?|该|此|这个|这票)?{cjk_parcel}", text):
            return True
        return False

    @staticmethod
    def _reply_matches_direct_answer(reply: str, answer: str) -> bool:
        reply_norm = OutputContracts._contract_match_text(reply)
        answer_norm = OutputContracts._contract_match_text(answer)
        if not reply_norm or not answer_norm:
            return False
        if len(answer_norm) >= 8 and answer_norm in reply_norm:
            return True
        if len(reply_norm) >= 8 and reply_norm in answer_norm:
            return True
        answer_numbers = OutputContracts._number_terms(answer)
        if answer_numbers and not answer_numbers.issubset(OutputContracts._number_terms(reply)):
            return False
        required_overlap = 2 if answer_numbers else 3
        return len(OutputContracts._meaningful_overlap_terms(reply, answer)) >= required_overlap

    @staticmethod
    def _contract_match_text(value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).lower()
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)

    @staticmethod
    def _meaningful_overlap_terms(reply: str, answer: str) -> set[str]:
        def terms(value: str) -> set[str]:
            normalized = unicodedata.normalize("NFKC", value or "").lower()
            latin = {item for item in re.findall(r"[a-z][a-z0-9_-]{2,}", normalized)}
            cjk = set()
            for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
                cjk.update(phrase[idx:idx + 2] for idx in range(0, max(0, len(phrase) - 1)))
            return latin | cjk

        return terms(reply) & terms(answer)
