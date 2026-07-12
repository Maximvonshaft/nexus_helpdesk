from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from ..models_knowledge_quarantine import KnowledgeIngestionRecord

_SAFE_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_PROMPT_RISK_PATTERNS = (
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|earlier)\s+(instructions?|rules?|polic(?:y|ies))\b", re.I),
    re.compile(r"\b(system|developer)\s+(prompt|message|instructions?)\b", re.I),
    re.compile(r"\b(reveal|print|exfiltrate|leak)\b.{0,40}\b(prompt|secret|credential|token|api[ _-]?key)\b", re.I | re.S),
    re.compile(r"\b(call|invoke|execute|run)\b.{0,30}\b(tool|function|shell|command)\b", re.I | re.S),
    re.compile(r"\b(bypass|override|disable)\b.{0,30}\b(safety|policy|approval|authorization)\b", re.I | re.S),
)
_HIDDEN_CONTENT_PATTERNS = (
    re.compile(r"<[^>]+(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)[^>]*>", re.I),
    re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]"),
)


@dataclass(frozen=True)
class InspectionResult:
    malware_status: str
    cdr_status: str
    reason: str
    scanner_identity: str
    safe_findings: dict[str, object]


class MalwareCdrAdapter(Protocol):
    def inspect(
        self,
        *,
        storage_key: str,
        content_sha256: str,
        declared_mime_type: str | None,
    ) -> InspectionResult:
        ...


class DisabledMalwareCdrAdapter:
    """Default adapter. Missing scanner capability never degrades to clean."""

    identity = "disabled-malware-cdr-v1"

    def inspect(
        self,
        *,
        storage_key: str,
        content_sha256: str,
        declared_mime_type: str | None,
    ) -> InspectionResult:
        del storage_key, content_sha256, declared_mime_type
        return InspectionResult(
            malware_status="unavailable",
            cdr_status="unavailable",
            reason="scanner.unavailable",
            scanner_identity=self.identity,
            safe_findings={"available": False},
        )


@dataclass(frozen=True)
class PublicationEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    safe_evidence: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "safe_evidence": dict(self.safe_evidence),
        }


def safe_reason(value: object, *, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_")[:120]
    return normalized if _SAFE_REASON_RE.fullmatch(normalized) else fallback


def classify_prompt_risk(text: str) -> tuple[str, tuple[str, ...]]:
    bounded = str(text or "")[:200_000]
    reasons: list[str] = []
    if any(pattern.search(bounded) for pattern in _PROMPT_RISK_PATTERNS):
        reasons.append("prompt_risk.instruction_like_content")
    if any(pattern.search(bounded) for pattern in _HIDDEN_CONTENT_PATTERNS):
        reasons.append("prompt_risk.hidden_content")
    if not reasons:
        return "clear", ()
    return "review", tuple(dict.fromkeys(reasons))


def evaluate_publication_eligibility(record: KnowledgeIngestionRecord) -> PublicationEligibility:
    reasons: list[str] = []
    if record.lifecycle_status != "approved":
        reasons.append("quarantine.lifecycle_not_approved")
    if record.signature_status != "match":
        reasons.append("quarantine.signature_not_verified")
    if record.malware_status != "clean":
        reasons.append("quarantine.malware_not_clean")
    # A CDR result of ``sanitized`` refers to a different derived artifact. This
    # first contract has no derived-content hash, so only the original artifact
    # being clean may authorize publication. A future CDR delivery must bind a
    # sanitized artifact hash before widening this condition.
    if record.cdr_status != "clean":
        reasons.append("quarantine.cdr_not_clean")
    if record.prompt_risk_status != "clear":
        reasons.append("quarantine.prompt_risk_not_clear")
    if record.source_trust not in {"internal_reviewed", "external_verified"}:
        reasons.append("quarantine.source_not_trusted")
    if record.review_status != "approved" or record.reviewed_by is None or record.reviewed_at is None:
        reasons.append("quarantine.human_review_missing")
    if not record.parser_name or not record.parser_version:
        reasons.append("quarantine.parser_identity_missing")
    if len(str(record.content_sha256 or "")) != 64:
        reasons.append("quarantine.content_hash_invalid")

    safe_evidence: dict[str, object] = {
        "record_id": record.id,
        "knowledge_item_id": record.knowledge_item_id,
        "tenant_key": str(record.tenant_key or "")[:80],
        "content_sha256": str(record.content_sha256 or "")[:64],
        "lifecycle_status": str(record.lifecycle_status or "")[:40],
        "signature_status": str(record.signature_status or "")[:40],
        "malware_status": str(record.malware_status or "")[:40],
        "cdr_status": str(record.cdr_status or "")[:40],
        "prompt_risk_status": str(record.prompt_risk_status or "")[:40],
        "source_trust": str(record.source_trust or "")[:40],
        "review_status": str(record.review_status or "")[:40],
        "parser_identity": f"{record.parser_name or 'missing'}:{record.parser_version or 'missing'}"[:160],
    }
    return PublicationEligibility(
        eligible=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        safe_evidence=safe_evidence,
    )
