from __future__ import annotations

from typing import Any

from . import codex_direct as _codex_direct
from ..schemas import ProviderRequest

# PR411 adds a local CH-format precheck that marks tracking lookups as
# format_invalid before any external Speedaf call. That is still a no-evidence
# tracking guidance case and must reuse PR410's compact Codex prompt budget.
# Keep this as a small compatibility patch instead of widening provider routing,
# policy gates, or Speedaf behavior.
_ADDITIONAL_COMPACT_NO_EVIDENCE_STATUSES = {
    "format_invalid",
    "negative_cache_hit",
}
_ADDITIONAL_COMPACT_NO_EVIDENCE_REASONS = {
    "invalid_ch_waybill_format",
    "tracking_fact_negative_cache_hit",
}
_ORIGINAL_SHOULD_COMPACT = _codex_direct._should_compact_no_evidence_prompt


def _should_compact_no_evidence_prompt_with_format_invalid(
    *,
    request: ProviderRequest,
    knowledge_context: dict[str, Any],
    tracking_fact_metadata: dict[str, Any],
) -> bool:
    if _ORIGINAL_SHOULD_COMPACT(
        request=request,
        knowledge_context=knowledge_context,
        tracking_fact_metadata=tracking_fact_metadata,
    ):
        return True
    if request.scenario != "webchat_fast_reply" or request.tracking_fact_evidence_present:
        return False
    if bool(tracking_fact_metadata.get("fact_evidence_present")):
        return False
    tool_status = str(tracking_fact_metadata.get("tool_status") or "").strip().lower()
    failure_reason = str(
        tracking_fact_metadata.get("failure_reason")
        or tracking_fact_metadata.get("tracking_fact_failure_reason")
        or ""
    ).strip().lower()
    if tool_status not in _ADDITIONAL_COMPACT_NO_EVIDENCE_STATUSES and failure_reason not in _ADDITIONAL_COMPACT_NO_EVIDENCE_REASONS:
        return False
    return bool(_codex_direct._compact_knowledge_hits(knowledge_context, limit=1))


if not getattr(_codex_direct, "_format_invalid_prompt_budget_patch_applied", False):
    _codex_direct._should_compact_no_evidence_prompt = _should_compact_no_evidence_prompt_with_format_invalid
    _codex_direct._format_invalid_prompt_budget_patch_applied = True
