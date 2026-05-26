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
    "compensation", "refund", "claim", "legal", "lawsuit", "account risk", "driver phone",
    "courier phone", "api", "token", "secret", "password", "internal system", "赔偿", "理赔",
    "退款", "法律", "起诉", "账号风险", "司机电话", "快递员电话", "接口", "令牌", "密钥", "内部系统",
)
PROMISE_MARKERS = (
    "guarantee", "guaranteed", "promise", "will deliver", "will refund", "一定", "保证", "承诺会",
)


@dataclass(frozen=True)
class GroundingDecision:
    applied: bool
    reply: str | None = None
    reason: str | None = None
    source: dict[str, Any] | None = None

    def as_trace(self) -> dict[str, Any]:
        return asdict(self)


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


def select_grounding_candidate(
    *,
    query: str,
    hits: list[KnowledgeChunkHit] | list[dict[str, Any]],
    tracking_fact_evidence_present: bool = False,
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
    text = (query or "").lower()
    if any(marker in text for marker in UNSAFE_MARKERS):
        return True
    if TRACKING_NUMBER_RE.search(text):
        return True
    if any(marker in text for marker in LIVE_TRACKING_MARKERS):
        return True
    return False


def _unsafe_answer(answer: str) -> bool:
    text = answer.lower()
    return any(marker in text for marker in UNSAFE_MARKERS) or any(marker in text for marker in PROMISE_MARKERS)
