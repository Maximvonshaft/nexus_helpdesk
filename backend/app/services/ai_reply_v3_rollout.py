from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

AI_REPLY_CONTRACT_V2 = "nexus.ai_reply.v2"
AI_REPLY_CONTRACT_V3 = "nexus.ai_reply.v3"
HIGH_RISK_TERMS = (
    "refund",
    "compensation",
    "customs",
    "tax",
    "delivery time",
    "tracking status",
    "赔付",
    "赔偿",
    "退款",
    "清关",
    "税",
    "时效",
    "物流状态",
)
TOOL_SOURCE_MARKERS = ("tool:", "tool.", "runtime_tool:", "tracking_tool:")
KB_SOURCE_MARKERS = ("kb:", "knowledge:", "knowledge.")
AUTHORITATIVE_MARKERS = ("tool:", "tool.", "official_policy", "authority:official_policy")


@dataclass(frozen=True)
class AIReplyV3RolloutConfig:
    enabled: bool = False
    channels: tuple[str, ...] = ("webchat",)
    greeting_enabled: bool = False
    handoff_enabled: bool = True
    tool_answer_enabled: bool = False
    kb_answer_enabled: bool = False
    strict_answer_grounding: bool = True
    confidence_threshold: float = 0.6


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    cleaned = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    return cleaned or tuple(item.strip().lower() for item in default.split(",") if item.strip())


def load_ai_reply_v3_rollout_config() -> AIReplyV3RolloutConfig:
    try:
        confidence = float(os.getenv("AI_REPLY_V3_CONFIDENCE_THRESHOLD", "0.6"))
    except ValueError:
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))
    return AIReplyV3RolloutConfig(
        enabled=_env_bool("AI_REPLY_V3_ENABLED", False),
        channels=_env_csv("AI_REPLY_V3_CHANNELS", "webchat"),
        greeting_enabled=_env_bool("AI_REPLY_V3_GREETING_ENABLED", False),
        handoff_enabled=_env_bool("AI_REPLY_V3_HANDOFF_ENABLED", True),
        tool_answer_enabled=_env_bool("AI_REPLY_V3_TOOL_ANSWER_ENABLED", False),
        kb_answer_enabled=_env_bool("AI_REPLY_V3_KB_ANSWER_ENABLED", False),
        strict_answer_grounding=_env_bool("AI_REPLY_V3_STRICT_ANSWER_GROUNDING", True),
        confidence_threshold=confidence,
    )


def choose_reply_contract_version(*, reply_type: str, channel: str | None, used_sources: Iterable[str] | None = None, config: AIReplyV3RolloutConfig | None = None) -> str:
    cfg = config or load_ai_reply_v3_rollout_config()
    if not cfg.enabled:
        return AI_REPLY_CONTRACT_V2
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel not in cfg.channels:
        return AI_REPLY_CONTRACT_V2
    if reply_type == "handoff_notice" and cfg.handoff_enabled:
        return AI_REPLY_CONTRACT_V3
    if reply_type == "clarifying_question" and cfg.greeting_enabled:
        return AI_REPLY_CONTRACT_V3
    if reply_type == "answer":
        sources = tuple(str(item or "") for item in (used_sources or ()))
        if cfg.tool_answer_enabled and _has_tool_source(sources):
            return AI_REPLY_CONTRACT_V3
        if cfg.kb_answer_enabled and _has_kb_source(sources):
            return AI_REPLY_CONTRACT_V3
    return AI_REPLY_CONTRACT_V2


def validate_v3_rollout_payload(
    *,
    reply_type: str,
    channel: str | None,
    used_sources: Iterable[str] | None = None,
    unsupported_claims: Iterable[str] | None = None,
    text: str | None = None,
    confidence: float | None = None,
    authority_level: str | None = None,
    effective_country: str | None = None,
    country_source: str | None = None,
    config: AIReplyV3RolloutConfig | None = None,
) -> str | None:
    cfg = config or load_ai_reply_v3_rollout_config()
    sources = tuple(str(item or "") for item in (used_sources or ()))
    claims = tuple(str(item or "") for item in (unsupported_claims or ()))
    normalized_channel = (channel or "").strip().lower()

    if normalized_channel and normalized_channel not in cfg.channels:
        return "ai_reply_v3_channel_not_enabled"
    if reply_type == "null_reply":
        return None
    if claims:
        if reply_type == "handoff_notice":
            return "ai_reply_v3_handoff_notice_unsupported_claims_blocked"
        return "ai_reply_v3_unsupported_claims_blocked"
    if reply_type == "handoff_notice":
        return None if cfg.handoff_enabled else "ai_reply_v3_handoff_notice_not_enabled"
    if reply_type == "clarifying_question":
        return None
    if reply_type != "answer":
        return "ai_reply_v3_reply_type_invalid"
    if cfg.strict_answer_grounding and not sources:
        return "ai_reply_v3_answer_requires_used_sources"
    if not (_has_tool_source(sources) or _has_kb_source(sources)):
        return "ai_reply_v3_answer_requires_tool_or_kb_source"
    if _has_tool_source(sources):
        if not cfg.tool_answer_enabled and cfg.enabled:
            return "ai_reply_v3_tool_answer_not_enabled"
    elif _has_kb_source(sources):
        if not cfg.kb_answer_enabled and cfg.enabled:
            return "ai_reply_v3_kb_answer_not_enabled"
        if not effective_country:
            return "ai_reply_v3_kb_answer_requires_effective_country"
        if country_source is None:
            return "ai_reply_v3_kb_answer_requires_country_source"
    if confidence is not None and confidence < cfg.confidence_threshold:
        return "ai_reply_v3_answer_confidence_below_threshold"
    if _is_high_risk(text or "") and not _has_authoritative_source(sources, authority_level):
        return "ai_reply_v3_high_risk_answer_requires_authoritative_source"
    return None


def _has_tool_source(sources: Iterable[str]) -> bool:
    joined = " ".join(sources).lower()
    return any(marker in joined for marker in TOOL_SOURCE_MARKERS)


def _has_kb_source(sources: Iterable[str]) -> bool:
    joined = " ".join(sources).lower()
    return any(marker in joined for marker in KB_SOURCE_MARKERS)


def _has_authoritative_source(sources: Iterable[str], authority_level: str | None) -> bool:
    if (authority_level or "").strip().lower() in {"tool", "official_policy"}:
        return True
    joined = " ".join(sources).lower()
    return any(marker in joined for marker in AUTHORITATIVE_MARKERS)


def _is_high_risk(text: str) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in HIGH_RISK_TERMS)
