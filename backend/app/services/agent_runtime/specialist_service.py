from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from ...models_agent_control import AgentRun, AgentRunEvent, AgentRunSnapshot
from .specialist_schemas import SpecialistFinding, SpecialistResult

_ALLOWED_SPECIALISTS = frozenset(
    {
        "knowledge_researcher",
        "policy_reviewer",
        "case_summarizer",
        "translation_reviewer",
        "data_analyst",
    }
)


def run_read_only_specialists(
    db: Session,
    *,
    parent_run: AgentRun,
    specialists: Iterable[str],
) -> list[dict[str, Any]]:
    """Return bounded evidence reviews without model calls or Tool side effects.

    Specialists consume only the canonical content-safe Agent Run/Event stream and
    immutable release snapshot evidence. They never read raw conversation text,
    customer-visible replies, Tool arguments/results, credentials or hidden
    reasoning. The parent Agent remains the sole decision and response authority.
    """

    selected: list[str] = []
    for raw in specialists:
        name = str(raw or "").strip().lower()
        if name not in _ALLOWED_SPECIALISTS:
            raise ValueError("agent_specialist_not_allowed")
        if name not in selected:
            selected.append(name)
    if len(selected) > 3:
        raise ValueError("agent_specialist_limit_exceeded")
    if not selected:
        return []

    events = (
        db.query(AgentRunEvent)
        .filter(AgentRunEvent.run_id == parent_run.id)
        .order_by(AgentRunEvent.sequence.asc())
        .all()
    )
    snapshot = (
        db.query(AgentRunSnapshot)
        .filter(AgentRunSnapshot.request_id == parent_run.request_id)
        .one_or_none()
    )
    return [
        _build_result(
            name,
            parent_run=parent_run,
            events=events,
            snapshot=snapshot,
        ).model_dump(exclude_none=True)
        for name in selected
    ]


def _build_result(
    name: str,
    *,
    parent_run: AgentRun,
    events: list[AgentRunEvent],
    snapshot: AgentRunSnapshot | None,
) -> SpecialistResult:
    if name == "knowledge_researcher":
        return _knowledge_result(parent_run, snapshot)
    if name == "policy_reviewer":
        return _policy_result(parent_run, events)
    if name == "case_summarizer":
        return _case_result(parent_run, events)
    if name == "translation_reviewer":
        return _translation_result(parent_run)
    return _data_result(parent_run, events)


def _knowledge_result(
    run: AgentRun,
    snapshot: AgentRunSnapshot | None,
) -> SpecialistResult:
    snapshot_json = snapshot.snapshot_json if snapshot and isinstance(snapshot.snapshot_json, dict) else {}
    resolved = snapshot_json.get("resolved") if isinstance(snapshot_json.get("resolved"), dict) else {}
    rows = resolved.get("knowledge") if isinstance(resolved.get("knowledge"), list) else []
    findings: list[SpecialistFinding] = []
    for item in rows[:12]:
        if not isinstance(item, dict):
            continue
        item_key = str(item.get("item_key") or "").strip()[:160]
        version = item.get("version")
        if item_key:
            findings.append(
                SpecialistFinding(
                    claim=f"Release includes approved knowledge {item_key} at version {version}.",
                    confidence=1.0,
                    evidence_refs=[f"agent_run_snapshot:{snapshot.id}" if snapshot else f"agent_run:{run.id}"],
                )
            )
    return SpecialistResult(
        specialist="knowledge_researcher",
        summary=(
            f"The immutable Agent Release contains {len(rows)} approved knowledge reference(s)."
            if snapshot
            else "No immutable Agent Run snapshot evidence is available."
        ),
        findings=findings,
        risks=[] if snapshot else ["release_snapshot_evidence_unavailable"],
        recommended_action=(
            "Use only Release-bound knowledge and canonical knowledge.search observations."
        ),
        needs_human_review=snapshot is None,
    )


def _policy_result(run: AgentRun, events: list[AgentRunEvent]) -> SpecialistResult:
    tool_events = [
        event
        for event in events
        if event.event_type in {"tool_authorized", "tool_completed", "tool_failed"}
    ]
    risks: list[str] = []
    findings: list[SpecialistFinding] = []
    for event in tool_events[:12]:
        payload = _payload(event)
        status = str(payload.get("status") or event.status)[:40]
        tool_name = str(payload.get("tool_name") or "unknown")[:160]
        if status in {"blocked", "confirmation_required", "failed"}:
            risks.append(f"{tool_name}:{status}")
        findings.append(
            SpecialistFinding(
                claim=f"Tool {tool_name} recorded policy/runtime status {status}.",
                confidence=1.0,
                evidence_refs=[f"agent_run_event:{event.id}"],
            )
        )
    return SpecialistResult(
        specialist="policy_reviewer",
        summary=f"Reviewed {len(tool_events)} governed Tool lifecycle event(s) for Run {run.id}.",
        findings=findings,
        risks=_dedupe(risks),
        recommended_action=(
            "Do not claim an action succeeded unless a committed tool_completed event exists."
        ),
        needs_human_review=bool(risks),
    )


def _case_result(run: AgentRun, events: list[AgentRunEvent]) -> SpecialistResult:
    tool_terminal = [
        event for event in events if event.event_type in {"tool_completed", "tool_failed"}
    ]
    findings = [
        SpecialistFinding(
            claim=(
                f"Run status is {run.status}; final action is "
                f"{run.final_action or 'unavailable'}; elapsed time is {run.elapsed_ms} ms."
            ),
            confidence=1.0,
            evidence_refs=[f"agent_run:{run.id}"],
        )
    ]
    for event in tool_terminal[:10]:
        payload = _payload(event)
        findings.append(
            SpecialistFinding(
                claim=(
                    f"Tool {payload.get('tool_name') or 'unknown'} finished with "
                    f"status {payload.get('status') or event.status}."
                ),
                confidence=1.0,
                evidence_refs=[f"agent_run_event:{event.id}"],
            )
        )
    return SpecialistResult(
        specialist="case_summarizer",
        summary=(
            f"Run {run.id} completed as {run.status} with {len(tool_terminal)} "
            "recorded terminal Tool event(s)."
        ),
        findings=findings,
        risks=[run.error_code] if run.error_code else [],
        recommended_action="Use the event references as the operational source of truth.",
        needs_human_review=run.status in {"failed", "fallback", "cancelled"},
    )


def _translation_result(run: AgentRun) -> SpecialistResult:
    return SpecialistResult(
        specialist="translation_reviewer",
        summary=(
            "Translation content is intentionally unavailable because Agent Run "
            "evidence does not persist raw customer messages or replies."
        ),
        findings=[
            SpecialistFinding(
                claim="The Run evidence boundary is content-free by design.",
                confidence=1.0,
                evidence_refs=[f"agent_run:{run.id}"],
            )
        ],
        risks=["source_text_not_persisted"],
        recommended_action=(
            "Perform translation review only inside a new read-only Playground fork "
            "with operator-supplied text."
        ),
        needs_human_review=True,
    )


def _data_result(run: AgentRun, events: list[AgentRunEvent]) -> SpecialistResult:
    provider_events = [
        event
        for event in events
        if event.event_type in {"provider_completed", "provider_failed"}
    ]
    tool_events = [
        event for event in events if event.event_type in {"tool_completed", "tool_failed"}
    ]
    provider_ms = sum(max(0, int(event.duration_ms or 0)) for event in provider_events)
    tool_ms = sum(max(0, int(event.duration_ms or 0)) for event in tool_events)
    findings = [
        SpecialistFinding(
            claim=f"Provider lifecycle recorded {len(provider_events)} event(s) and {provider_ms} ms.",
            confidence=1.0,
            evidence_refs=[f"agent_run:{run.id}"],
        ),
        SpecialistFinding(
            claim=f"Tool lifecycle recorded {len(tool_events)} terminal event(s) and {tool_ms} ms.",
            confidence=1.0,
            evidence_refs=[f"agent_run:{run.id}"],
        ),
    ]
    return SpecialistResult(
        specialist="data_analyst",
        summary=f"Run {run.id} total elapsed time is {run.elapsed_ms} ms.",
        findings=findings,
        risks=[run.error_code] if run.error_code else [],
        recommended_action="Compare these low-cardinality metrics across Release cohorts.",
        needs_human_review=False,
    )


def _payload(event: AgentRunEvent) -> dict[str, Any]:
    return event.safe_payload_json if isinstance(event.safe_payload_json, dict) else {}


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()[:240]
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output[:12]
