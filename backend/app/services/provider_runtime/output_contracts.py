from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import jsonschema

_SECRET_PATTERNS = [
    re.compile(("s" + "k-") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("ey" + "J") + r"[A-Za-z0-9_\-]{12,}"),
    re.compile(("Bear" + "er") + r"\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
]
_INTERNAL_PATTERNS = ["localhost", "127.0.0.1", "::1", "bridge", "external_channel", "provider_runtime"]
_STATUS_WORDS = [
    "delivered", "in transit", "out for delivery", "customs", "returned", "failed delivery",
    "派送", "已签收", "运输中", "清关", "退回",
]
_MAX_RUNTIME_REPLY_CHARS = 1200
_COUNTRY_CONFLICT_GROUPS = (
    ("nigeria", "尼日利亚"),
    ("switzerland", "swiss", "瑞士"),
    ("china", "chinese", "中国"),
    ("uk", "britain", "英国"),
    ("usa", "america", "美国"),
    ("ghana", "加纳"),
    ("kenya", "肯尼亚"),
    ("uae", "阿联酋"),
    ("saudi", "沙特"),
)
_SERVICE_CONFLICT_GROUPS = (
    ("ocean shipping", "sea shipping", "ocean", "sea freight", "海运"),
    ("air shipping", "air freight", "air", "空运"),
    ("address change", "address-change", "改地址", "地址变更"),
    ("customs clearance", "customs", "清关"),
    ("redelivery", "reattempt", "重派", "重新派送"),
    ("currently unavailable", "not available", "not provide", "does not provide", "do not provide", "unavailable", "暂未开通", "未开通", "不支持"),
    ("we provide", "we offer", "we support", "service is available", "available in", "已开通", "支持"),
)


class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict[str, Any]:
        if contract_name == "nexus.ai_reply.v2":
            return {
                "type": "object",
                "properties": {
                    "customer_reply": {"type": "string", "maxLength": 1200},
                    "language": {"type": "string", "maxLength": 32},
                    "intent": {"type": "string", "maxLength": 80},
                    "tracking_number": {"type": ["string", "null"], "maxLength": 80},
                    "handoff_required": {"type": "boolean"},
                    "handoff_reason": {"type": ["string", "null"], "maxLength": 500},
                    "recommended_agent_action": {"type": ["string", "null"], "maxLength": 500},
                    "ticket_should_create": {"type": "boolean"},
                    "internal_summary": {"type": ["string", "null"], "maxLength": 1000},
                    "risk_flags": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 20},
                    "runtime_trace_id": {"type": "string", "maxLength": 120},
                    "contract_version": {"type": "string", "const": "nexus.ai_reply.v2"},
                    "runtime_signature": {"type": "string", "minLength": 32, "maxLength": 128},
                    "safety_status": {"type": "string", "enum": ["passed", "reviewed"]},
                    "origin": {"type": "string", "enum": ["provider_runtime", "ai_runtime"]},
                },
                "required": [
                    "customer_reply",
                    "language",
                    "intent",
                    "handoff_required",
                    "ticket_should_create",
                    "runtime_trace_id",
                    "contract_version",
                    "runtime_signature",
                    "safety_status",
                    "origin",
                ],
                "additionalProperties": False,
            }
        if contract_name == "nexus_webchat_runtime_reply_v1":
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
        if contract_name == "nexus_webchat_runtime_reply_v1":
            parsed = OutputContracts._normalize_runtime_reply_v1(parsed)
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
        return parsed

    @staticmethod
    def locked_fact_validation(
        reply: Any,
        knowledge_context: dict[str, Any] | None,
        *,
        request_body: Any = None,
    ) -> dict[str, Any]:
        locked_facts = OutputContracts._locked_facts(knowledge_context)
        ids = [str(fact.get("item_key") or "") for fact in locked_facts if fact.get("item_key")]
        if not locked_facts:
            return {"status": "not_applicable", "locked_fact_ids": []}
        reply_text = str(reply or "").strip()
        if not reply_text:
            return {"status": "fail", "reason": "empty_reply", "locked_fact_ids": ids}
        for fact in locked_facts:
            answer = str(fact.get("answer") or "").strip()
            if answer and OutputContracts._locked_fact_conflict(reply_text, answer):
                return {
                    "status": "fail",
                    "reason": "reply_conflicts_with_locked_fact",
                    "locked_fact_ids": [str(fact.get("item_key") or "")] if fact.get("item_key") else ids,
                    "source": fact.get("source") if isinstance(fact.get("source"), dict) else {"item_key": fact.get("item_key"), "title": fact.get("title")},
                }
        for fact in locked_facts:
            answer = str(fact.get("answer") or "").strip()
            if not answer:
                continue
            if OutputContracts._reply_matches_direct_answer(reply_text, answer, request_body=request_body):
                return {
                    "status": "pass",
                    "locked_fact_ids": [str(fact.get("item_key") or "")] if fact.get("item_key") else ids,
                    "source": fact.get("source") if isinstance(fact.get("source"), dict) else {"item_key": fact.get("item_key"), "title": fact.get("title")},
                }
        return {"status": "fail", "reason": "reply_not_fact_equivalent", "locked_fact_ids": ids}

    @staticmethod
    def _normalize_runtime_reply_v1(parsed: dict[str, Any]) -> dict[str, Any]:
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
    def _truncate_reply(value: str) -> str:
        cleaned = " ".join(str(value or "").strip().split())
        if len(cleaned) <= _MAX_RUNTIME_REPLY_CHARS:
            return cleaned
        return cleaned[: _MAX_RUNTIME_REPLY_CHARS - 3].rstrip() + "..."

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
            raise ValueError("Parcel status language requires trusted tracking evidence")
        if (
            parsed.get("handoff_required") is not True
            and not OutputContracts._trusted_tracking_reply_can_bypass_locked_facts(
                evidence_present=evidence_present,
                request_body=request_body,
                parsed=parsed,
            )
        ):
            validation = OutputContracts.locked_fact_validation(reply, knowledge_context, request_body=request_body)
            if validation["status"] == "fail":
                raise ValueError("Locked fact grounding conflict")

    @staticmethod
    def _trusted_tracking_reply_can_bypass_locked_facts(
        *,
        evidence_present: bool,
        request_body: Any,
        parsed: dict[str, Any] | None,
    ) -> bool:
        if not evidence_present:
            return False
        text = str(request_body or "").strip().lower()
        if not text:
            return False
        if OutputContracts._looks_like_service_or_policy_question(text):
            return False
        intent = str((parsed or {}).get("intent") or "").strip().lower()
        if intent in {"tracking", "tracking_status", "tracking_unresolved", "delivery_issue", "logistics"}:
            return True
        logistics_markers = (
            "track",
            "tracking",
            "parcel",
            "package",
            "shipment",
            "waybill",
            "delivery",
            "delivered",
            "recipient",
            "received",
            "receive",
            "not received",
            "did not receive",
            "where is",
            "status",
            "order",
            "单号",
            "运单",
            "物流",
            "快递",
            "包裹",
            "收件人",
            "没收到",
            "没有收到",
            "签收",
            "派送",
            "配送",
            "查件",
            "查询",
        )
        return any(marker in text for marker in logistics_markers)

    @staticmethod
    def _looks_like_service_or_policy_question(text: str) -> bool:
        service_markers = (
            "do you provide",
            "do you offer",
            "do you support",
            "is there",
            "is it available",
            "service available",
            "service availability",
            "domestic to domestic",
            "domestic-to-domestic",
            "local-to-local",
            "local delivery",
            "本对本",
            "本地到本地",
            "本地寄本地",
            "是否开通",
            "支持寄送",
            "可以寄",
        )
        return any(marker in text for marker in service_markers)

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
        return OutputContracts._reply_matches_direct_answer(
            reply,
            str(candidate.get("answer") or ""),
            request_body=request_body,
        )

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
    def _reply_matches_direct_answer(reply: str, answer: str, *, request_body: Any = None) -> bool:
        if OutputContracts._locked_fact_conflict(reply, answer):
            return False
        if not OutputContracts._reply_uses_answer_specific_terms(reply, answer, request_body=request_body):
            return False
        reply_norm = OutputContracts._contract_match_text(reply)
        answer_norm = OutputContracts._contract_match_text(answer)
        if not reply_norm or not answer_norm:
            return False
        if len(answer_norm) >= 8 and answer_norm in reply_norm:
            return True
        if len(reply_norm) >= 8 and reply_norm in answer_norm:
            return True
        answer_numbers = set(re.findall(r"\d+(?:\.\d+)?", answer_norm))
        if answer_numbers:
            if not answer_numbers.issubset(set(re.findall(r"\d+(?:\.\d+)?", reply_norm))):
                return False
            reply_numbers = set(re.findall(r"\d+(?:\.\d+)?", reply_norm))
            if reply_numbers - answer_numbers:
                return False
        return len(OutputContracts._meaningful_overlap_terms(reply, answer)) >= 2

    @staticmethod
    def _reply_uses_answer_specific_terms(reply: str, answer: str, *, request_body: Any = None) -> bool:
        answer_terms = OutputContracts._meaningful_overlap_terms(answer, answer)
        if not answer_terms:
            return True
        request_terms = OutputContracts._meaningful_overlap_terms(str(request_body or ""), str(request_body or ""))
        weak_terms = {
            "speedaf",
            "support",
            "customer",
            "service",
            "answer",
            "result",
            "help",
            "please",
            "物流",
            "客服",
            "客户",
            "知识",
            "闭环",
            "结果",
            "请问",
        }
        specific_terms = {
            term
            for term in answer_terms - request_terms
            if len(term) >= 2 and term not in weak_terms
        }
        if not specific_terms:
            return True
        reply_terms = OutputContracts._meaningful_overlap_terms(reply, reply)
        if bool(specific_terms & reply_terms):
            return True
        answer_numbers = OutputContracts._factual_numbers(answer)
        reply_numbers = OutputContracts._factual_numbers(reply)
        if answer_numbers and answer_numbers.issubset(reply_numbers) and not (reply_numbers - answer_numbers):
            return True
        return OutputContracts._answer_specific_group_covered(reply, answer, request_body=request_body)

    @staticmethod
    def _factual_numbers(value: str) -> set[str]:
        text = unicodedata.normalize("NFKC", str(value or ""))
        return set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])", text))

    @staticmethod
    def _answer_specific_group_covered(reply: str, answer: str, *, request_body: Any = None) -> bool:
        reply_text = OutputContracts._contract_match_text(reply)
        answer_text = OutputContracts._contract_match_text(answer)
        request_text = OutputContracts._contract_match_text(str(request_body or ""))
        if not reply_text or not answer_text:
            return False
        for group in (*_COUNTRY_CONFLICT_GROUPS, *_SERVICE_CONFLICT_GROUPS):
            answer_has_specific_group = any(
                OutputContracts._contract_match_text(term) in answer_text
                and OutputContracts._contract_match_text(term) not in request_text
                for term in group
            )
            if answer_has_specific_group and any(OutputContracts._contract_match_text(term) in reply_text for term in group):
                return True
        return False

    @staticmethod
    def _locked_facts(knowledge_context: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(knowledge_context, dict):
            return []
        facts = knowledge_context.get("locked_facts")
        if not isinstance(facts, list):
            return []
        return [fact for fact in facts if isinstance(fact, dict)]

    @staticmethod
    def _locked_fact_conflict(reply: str, answer: str) -> bool:
        reply_numbers = set(re.findall(r"\d+(?:\.\d+)?", OutputContracts._contract_match_text(reply)))
        answer_numbers = set(re.findall(r"\d+(?:\.\d+)?", OutputContracts._contract_match_text(answer)))
        if answer_numbers and (not answer_numbers.issubset(reply_numbers) or bool(reply_numbers - answer_numbers)):
            return True
        return OutputContracts._has_group_conflict(reply, answer, _COUNTRY_CONFLICT_GROUPS) or OutputContracts._has_group_conflict(reply, answer, _SERVICE_CONFLICT_GROUPS)

    @staticmethod
    def _has_group_conflict(reply: str, answer: str, groups: tuple[tuple[str, ...], ...]) -> bool:
        reply_text = OutputContracts._contract_match_text(reply)
        answer_text = OutputContracts._contract_match_text(answer)
        if not reply_text or not answer_text:
            return False
        answer_groups = {
            idx
            for idx, group in enumerate(groups)
            if any(OutputContracts._contract_match_text(term) in answer_text for term in group)
        }
        if not answer_groups:
            return False
        reply_groups = {
            idx
            for idx, group in enumerate(groups)
            if any(OutputContracts._contract_match_text(term) in reply_text for term in group)
        }
        return bool(reply_groups - answer_groups)

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
