from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..agent_control_config import RUNTIME_POLICY
from ..provider_runtime.output_contracts import AGENT_SPECIALIST_OUTPUT_CONTRACT
from ..provider_runtime.router import ProviderRuntimeRouter
from ..provider_runtime.schemas import ProviderRequest
from .specialist_schemas import SpecialistName, SpecialistResult


@dataclass(frozen=True)
class SpecialistExecutionResult:
    ok: bool
    specialist: str
    status: str
    evidence: dict[str, Any]
    elapsed_ms: int
    provider: str | None = None
    error_code: str | None = None

    def safe_summary(self) -> dict[str, Any]:
        return {
            "specialist": self.specialist,
            "status": self.status,
            "evidence": self.evidence,
            "elapsed_ms": self.elapsed_ms,
            "provider": self.provider,
            "error_code": self.error_code,
        }


async def run_specialist(
    db: Session,
    *,
    release_snapshot: dict[str, Any],
    tenant_key: str,
    channel_key: str,
    session_id: str,
    request_id: str,
    specialist: SpecialistName | str,
    task: str,
    evidence_refs: list[str] | None = None,
) -> SpecialistExecutionResult:
    """Run one bounded read-only specialist through the canonical Provider Router."""

    started = time.monotonic()
    specialist_name = _specialist_name(specialist)
    task_text = " ".join(str(task or "").split())[:3000]
    if not task_text:
        return _failure(
            specialist_name,
            "specialist_task_required",
            started,
        )
    refs = _evidence_refs(evidence_refs)
    timeout_ms = min(
        _runtime_timeout(release_snapshot),
        12000,
    )
    request = ProviderRequest(
        request_id=str(request_id or "specialist")[:160],
        tenant_id=str(tenant_key or "default")[:80],
        tenant_key=str(tenant_key or "default")[:80],
        channel_key=str(channel_key or "webchat")[:40],
        session_id=str(session_id or "specialist")[:160],
        scenario="agent_specialist",
        body=task_text,
        recent_context=None,
        output_contract=AGENT_SPECIALIST_OUTPUT_CONTRACT,
        timeout_ms=timeout_ms,
        metadata={
            "agent_specialist": specialist_name,
            "agent_specialist_evidence_refs": refs,
            "agent_release_snapshot": release_snapshot,
            "customer_language": "auto",
        },
    )
    result = await ProviderRuntimeRouter(db).route(request)
    if not result.ok or not isinstance(result.structured_output, dict):
        return _failure(
            specialist_name,
            result.error_code or "specialist_provider_unavailable",
            started,
            provider=result.provider,
        )
    try:
        evidence = SpecialistResult.model_validate(
            result.structured_output
        ).model_dump(exclude_none=True)
    except Exception:
        return _failure(
            specialist_name,
            "specialist_output_invalid",
            started,
            provider=result.provider,
        )
    if evidence.get("specialist") != specialist_name:
        return _failure(
            specialist_name,
            "specialist_identity_mismatch",
            started,
            provider=result.provider,
        )
    return SpecialistExecutionResult(
        ok=True,
        specialist=specialist_name,
        status="executed",
        evidence=evidence,
        elapsed_ms=_elapsed_ms(started),
        provider=result.provider,
    )


def run_specialist_sync(
    db: Session,
    **kwargs: Any,
) -> SpecialistExecutionResult:
    """Join the async Provider call without creating an unobserved background task."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_specialist(db, **kwargs))

    output: list[SpecialistExecutionResult] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            output.append(asyncio.run(run_specialist(db, **kwargs)))
        except BaseException as exc:  # pragma: no cover - re-raised on caller thread
            failure.append(exc)

    thread = threading.Thread(
        target=target,
        name="nexus-specialist-joined",
        daemon=False,
    )
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    if not output:
        raise RuntimeError("specialist_joined_execution_missing")
    return output[0]


def _runtime_timeout(release_snapshot: dict[str, Any]) -> int:
    if not isinstance(release_snapshot, dict) or release_snapshot.get("source") != "deployment":
        raise RuntimeError("agent_release_snapshot_required_for_specialist")
    resolved = release_snapshot.get("resolved")
    resources = resolved.get("resources") if isinstance(resolved, dict) else None
    if not isinstance(resources, list):
        raise RuntimeError("agent_release_resources_invalid")
    policies = [
        item.get("content")
        for item in resources
        if isinstance(item, dict) and item.get("config_type") == RUNTIME_POLICY
    ]
    if len(policies) != 1 or not isinstance(policies[0], dict):
        raise RuntimeError("agent_release_runtime_policy_ambiguous")
    try:
        value = int(policies[0].get("provider_timeout_ms") or 15000)
    except (TypeError, ValueError):
        value = 15000
    return max(1000, min(value, 120000))


def _specialist_name(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    allowed = {
        "knowledge_researcher",
        "policy_reviewer",
        "case_summarizer",
        "translation_reviewer",
        "data_analyst",
    }
    if candidate not in allowed:
        raise ValueError("specialist_name_invalid")
    return candidate


def _evidence_refs(value: list[str] | None) -> list[str]:
    output: list[str] = []
    for item in list(value or [])[:20]:
        cleaned = str(item or "").strip()[:160]
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _failure(
    specialist: str,
    error_code: str,
    started: float,
    *,
    provider: str | None = None,
) -> SpecialistExecutionResult:
    return SpecialistExecutionResult(
        ok=False,
        specialist=specialist,
        status="failed",
        evidence={},
        elapsed_ms=_elapsed_ms(started),
        provider=provider,
        error_code=str(error_code or "specialist_failed")[:160],
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
