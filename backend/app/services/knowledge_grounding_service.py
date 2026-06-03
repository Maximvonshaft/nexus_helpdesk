from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .knowledge_retrieval_service import DIRECT_ANSWER_SCORE_THRESHOLD, KnowledgeChunkHit

REFUSAL_MARKERS = (
    "cannot confirm",
    "can't confirm",
    "cannot verify",
    "can't verify",
    "not sure",
    "i don't know",
    "do not know",
    "unable to confirm",
    "unable to verify",
    "cannot provide",
    "support specialist will check",
    "support team will check",
    "无法确认",
    "不能确认",
    "无法核实",
    "不能核实",
    "不清楚",
    "不知道",
    "无法提供",
    "客服专员会核查",
)
LIVE_TRACKING_MARKERS = (
    "where is", "parcel status", "package status", "tracking status", "delivered", "in transit",
    "out for delivery", "customs", "returned", "failed delivery", "物流状态", "包裹状态", "快递状态",
    "在哪里", "到哪里", "派送", "签收", "妥投", "运输中", "清关", "退回",
)
TRACKING_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9]{8,30}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b", re.I)
NUMBER_RE = re.compile(r"(?<![A-Z0-9])\d+(?:\.\d+)?(?![A-Z0-9])", re.I)
UNSAFE_MARKERS = (
    "compensation", "refund", "claim", "complaint", "complain", "legal", "lawsuit", "account risk", "driver phone",
    "courier phone", "api", "token", "secret", "password", "internal system", "赔偿", "理赔",
    "退款", "投诉", "法律", "起诉", "账号风险", "司机电话", "快递员电话", "接口", "令牌", "密钥", "内部系统",
)
PROMISE_MARKERS = (
    "guarantee", "guaranteed", "promise", "will deliver", "will refund", "一定", "保证", "承诺会",
)
EXPLICIT_HANDOFF_OR_BUSINESS_ACTION_MARKERS = (
    "human", "agent", "representative", "manual review", "handoff", "hand off", "transfer",
    "escalate", "escalation", "complaint", "complain", "cancel", "cancellation", "refuse",
    "refusal", "return", "address change", "change address", "modify address", "refund", "claim",
    "compensation", "人工", "真人", "人工客服", "转人工", "客服接入", "升级", "投诉", "取消",
    "拒收", "拒签", "退回", "退货", "改地址", "地址变更", "更改地址", "修改地址", "退款", "赔偿", "理赔",
)


@dataclass(frozen=True)
class GroundingDecision:
    applied: bool
    reply: str | None = None
    reason: str | None = None
    source: dict[str, Any] | None = None

    def as_trace(self) -> dict[str, Any]:
        return asdict(self)


def is_explicit_handoff_or_business_action(query: str | None) -> bool:
    text = _unsafe_match_text(query)
    return bool(text and any(marker in text for marker in EXPLICIT_HANDOFF_OR_BUSINESS_ACTION_MARKERS))


def select_trusted_direct_answer_evidence(
    knowledge_context: dict[str, Any] | None,
    *,
    tracking_fact_evidence_present: bool = False,
) -> GroundingDecision:
    if tracking_fact_evidence_present:
        return GroundingDecision(applied=False, reason="tracking_fact_evidence_present")
    if not isinstance(knowledge_context, dict):
        return GroundingDecision(applied=False, reason="knowledge_context_missing")
    if knowledge_context.get("grounding_would_apply") is not True:
        return GroundingDecision(applied=False, reason="grounding_context_not_applicable")

    hits = knowledge_context.get("hits")
    if not isinstance(hits, list) or not hits:
        return GroundingDecision(applied=False, reason="knowledge_hits_missing")

    entity_terms = _query_entity_terms(knowledge_context)
    locked_facts = knowledge_context.get("locked_facts") if isinstance(knowledge_context.get("locked_facts"), list) else []
    grounding_source = knowledge_context.get("grounding_source") if isinstance(knowledge_context.get("grounding_source"), dict) else {}

    def has_citation_or_source_metadata(data: dict[str, Any], metadata: dict[str, Any], source: dict[str, Any]) -> bool:
        source_metadata = data.get("source_metadata") if isinstance(data.get("source_metadata"), dict) else {}
        if source_metadata:
            return True
        if isinstance(metadata.get("citation"), dict) and metadata.get("citation"):
            return True
        if isinstance(source.get("source_metadata"), dict) and source.get("source_metadata"):
            return True
        if isinstance(source.get("citation"), dict) and source.get("citation"):
            return True
        return False

    def candidate_from_hit(hit: KnowledgeChunkHit | dict[str, Any]) -> dict[str, Any] | None:
        data = _hit_dict(hit)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        candidate = select_grounding_candidate(
            query="",
            hits=[data],
            tracking_fact_evidence_present=False,
            required_entity_terms=entity_terms,
        )
        if not candidate:
            return None
        if not has_citation_or_source_metadata(data, metadata, candidate.get("source") or {}):
            return None
        return candidate

    source_item_key = str(grounding_source.get("item_key") or "")
    for hit in hits:
        data = _hit_dict(hit)
        if source_item_key and str(data.get("item_key") or "") != source_item_key:
            continue
        candidate = candidate_from_hit(data)
        if candidate:
            source = dict(candidate.get("source") or {})
            if grounding_source:
                source = {**source, **{key: value for key, value in grounding_source.items() if value not in (None, "", [], {})}}
            return GroundingDecision(applied=True, reply=candidate["answer"], reason="trusted_direct_answer_evidence", source=source)

    for fact in locked_facts:
        if not isinstance(fact, dict):
            continue
        answer = str(fact.get("answer") or "").strip()
        if not answer or fact.get("answer_mode") != "direct_answer" or _unsafe_answer(answer):
            continue
        fact_key = str(fact.get("item_key") or "")
        for hit in hits:
            data = _hit_dict(hit)
            if fact_key and str(data.get("item_key") or "") != fact_key:
                continue
            candidate = candidate_from_hit(data)
            if candidate and candidate.get("answer") == answer:
                return GroundingDecision(
                    applied=True,
                    reply=answer,
                    reason="trusted_locked_fact_direct_answer_evidence",
                    source=candidate.get("source"),
                )

    for hit in hits:
        candidate = candidate_from_hit(hit)
        if candidate:
            return GroundingDecision(
                applied=True,
                reply=candidate["answer"],
                reason="trusted_hit_direct_answer_evidence",
                source=candidate.get("source"),
            )

    return GroundingDecision(applied=False, reason="no_trusted_direct_answer")


def enforce_grounded_answer(
    *,
    query: str,
    provider_reply: str | None,
    hits: list[KnowledgeChunkHit] | list[dict[str, Any]],
    tracking_fact_evidence_present: bool = False,
) -> GroundingDecision:
    candidate = select_grounding_candidate(
        query=query,
        hits=hits,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    if candidate is None:
        return GroundingDecision(applied=False, reason="no_safe_direct_answer")
    if _looks_like_refusal(provider_reply):
        return GroundingDecision(applied=True, reply=candidate["answer"], reason="direct_answer_refusal_rewrite", source=candidate["source"])
    if _looks_like_direct_conflict(provider_reply=provider_reply, direct_answer=candidate["answer"]):
        return GroundingDecision(applied=True, reply=candidate["answer"], reason="direct_answer_conflict_rewrite", source=candidate["source"])
    return GroundingDecision(applied=False, reason="provider_reply_not_refusal_or_conflict", source=candidate["source"])


def select_approved_direct_answer_override(
    *,
    query: str,
    provider_output: dict[str, Any] | None,
    knowledge_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool = False,
) -> GroundingDecision:
    if not isinstance(knowledge_context, dict):
        return GroundingDecision(applied=False, reason="knowledge_context_missing")
    if knowledge_context.get("grounding_would_apply") is not True:
        return GroundingDecision(applied=False, reason="grounding_context_not_applicable")
    if not isinstance(knowledge_context.get("grounding_source"), dict) or not knowledge_context.get("grounding_source"):
        return GroundingDecision(applied=False, reason="grounding_source_missing")
    hits = knowledge_context.get("hits")
    if not isinstance(hits, list):
        return GroundingDecision(applied=False, reason="knowledge_hits_missing")
    entity_terms = _query_entity_terms(knowledge_context)
    candidate = select_grounding_candidate(
        query=query,
        hits=hits,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
        required_entity_terms=entity_terms,
    )
    if candidate is None:
        if entity_terms and select_grounding_candidate(
            query=query,
            hits=hits,
            tracking_fact_evidence_present=tracking_fact_evidence_present,
        ):
            return GroundingDecision(applied=False, reason="entity_mismatch")
        return GroundingDecision(applied=False, reason="no_safe_direct_answer")
    if _has_trusted_tracking_output_conflict(
        provider_output=provider_output,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    ):
        return GroundingDecision(applied=False, reason="trusted_tracking_output_conflict", source=candidate["source"])
    return GroundingDecision(
        applied=True,
        reply=candidate["answer"],
        reason="approved_direct_answer_override",
        source=candidate["source"],
    )


def select_grounding_candidate(
    *,
    query: str,
    hits: list[KnowledgeChunkHit] | list[dict[str, Any]],
    tracking_fact_evidence_present: bool = False,
    required_entity_terms: list[str] | None = None,
) -> dict[str, Any] | None:
    if _unsafe_for_grounding(query, tracking_fact_evidence_present=tracking_fact_evidence_present):
        return None
    for hit in hits:
        data = _hit_dict(hit)
        answer = str(data.get("direct_answer") or "").strip()
        if not answer:
            continue
        if _unsafe_answer(answer):
            continue
        score = _float(data.get("score"))
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        if score < DIRECT_ANSWER_SCORE_THRESHOLD:
            continue
        if metadata.get("knowledge_kind") not in {"faq", "business_fact"}:
            continue
        if metadata.get("fact_status") != "approved":
            continue
        if (data.get("answer_mode") or metadata.get("answer_mode")) != "direct_answer":
            continue
        entity_text = _candidate_entity_text(data=data, answer=answer, metadata=metadata)
        if required_entity_terms and not _entity_terms_compatible(required_entity_terms, entity_text):
            continue
        return {
            "answer": answer,
            "source": {
                "item_key": data.get("item_key"),
                "title": data.get("title"),
                "score": score,
                "chunk_index": data.get("chunk_index"),
                "retrieval_method": data.get("retrieval_method") or metadata.get("retrieval_method"),
                "source_metadata": data.get("source_metadata") or {},
            },
            "_entity_text": entity_text,
        }
    return None


def _hit_dict(hit: KnowledgeChunkHit | dict[str, Any]) -> dict[str, Any]:
    if isinstance(hit, dict):
        return hit
    return {
        "item_key": hit.item_key,
        "title": hit.title,
        "score": hit.score,
        "chunk_index": hit.chunk_index,
        "direct_answer": hit.direct_answer,
        "answer_mode": hit.answer_mode,
        "retrieval_method": hit.retrieval_method,
        "metadata": hit.metadata,
        "source_metadata": hit.source_metadata,
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _looks_like_refusal(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return bool(text and any(marker in text for marker in REFUSAL_MARKERS))


def _looks_like_direct_conflict(*, provider_reply: str | None, direct_answer: str) -> bool:
    reply_numbers = _number_terms(provider_reply)
    answer_numbers = _number_terms(direct_answer)
    if not reply_numbers or not answer_numbers:
        return False
    return answer_numbers.isdisjoint(reply_numbers)


def _has_trusted_tracking_output_conflict(
    *,
    provider_output: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> bool:
    if not tracking_fact_evidence_present or not isinstance(provider_output, dict):
        return False
    intent = str(provider_output.get("intent") or "").strip()
    if intent == "tracking":
        return True
    return bool(provider_output.get("tracking_number"))


def _query_entity_terms(knowledge_context: dict[str, Any]) -> list[str]:
    query_analysis = knowledge_context.get("query_analysis")
    if not isinstance(query_analysis, dict):
        return []
    raw_terms = query_analysis.get("entity_terms")
    if not isinstance(raw_terms, list):
        return []
    return [str(term).strip() for term in raw_terms if str(term).strip()]


def _candidate_entity_text(*, data: dict[str, Any], answer: str, metadata: dict[str, Any]) -> str:
    source_metadata = data.get("source_metadata") if isinstance(data.get("source_metadata"), dict) else {}
    parts = [
        data.get("item_key"),
        data.get("title"),
        data.get("text"),
        answer,
        metadata.get("item_key"),
        metadata.get("title"),
        source_metadata.get("item_key"),
        source_metadata.get("title"),
    ]
    return " ".join(str(part) for part in parts if part not in (None, ""))


def _entity_terms_compatible(entity_terms: list[str], candidate_text: str) -> bool:
    normalized_candidate = _entity_match_text(candidate_text)
    if not normalized_candidate:
        return False
    for term in entity_terms:
        normalized_term = _entity_match_text(term)
        if normalized_term and normalized_term in normalized_candidate:
            return True
    return False


def _entity_match_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _number_terms(value: str | None) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value or "")
    terms: set[str] = set()
    for match in NUMBER_RE.finditer(normalized):
        try:
            decimal = Decimal(match.group(0))
        except InvalidOperation:
            continue
        terms.add(str(decimal.normalize()).lower())
    return terms


def _unsafe_for_grounding(query: str | None, *, tracking_fact_evidence_present: bool) -> bool:
    text = _unsafe_match_text(query)
    if any(marker in text for marker in UNSAFE_MARKERS):
        return True
    if TRACKING_NUMBER_RE.search(text):
        return True
    if any(marker in text for marker in LIVE_TRACKING_MARKERS):
        return True
    return False


def _unsafe_answer(answer: str) -> bool:
    text = _unsafe_match_text(answer)
    return any(marker in text for marker in UNSAFE_MARKERS) or any(marker in text for marker in PROMISE_MARKERS)


def _unsafe_match_text(value: str | None) -> str:
    return re.sub(r"[\s_-]+", " ", (value or "").lower())
