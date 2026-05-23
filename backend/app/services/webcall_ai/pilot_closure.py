from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceAIAction, WebchatVoiceSession
from .config import get_webcall_ai_settings
from .evidence_builder import build_webcall_ai_evidence_report
from .pilot_canary_gate import evaluate_webcall_ai_pilot_gate
from .pilot_fake_tracking import fake_tracking_fact_for_pilot
from .pilot_session_source import resolve_or_create_pilot_voice_session
from .real_media_smoke import run_webcall_ai_real_media_smoke
from .worker import run_webcall_ai_worker_once


@dataclass(frozen=True)
class WebCallAIPilotClosureResult:
    ok: bool
    mode: str
    claimed: int
    transcript_segments: int
    turns: int
    actions: int
    tts_runtime_events: int
    voice_egress_sent: int
    voice_egress_failures: int
    handoff_events: int
    evidence_report: int
    error_code: str | None = None


def run_webcall_ai_pilot_closure_once(
    db: Session,
    *,
    worker_id: str,
    mode: str | None = None,
    tenant_key: str | None = None,
) -> WebCallAIPilotClosureResult:
    settings = get_webcall_ai_settings()
    selected_mode = mode or settings.pilot_mode
    effective_tenant_key = tenant_key or _first_csv(settings.pilot_tenant_allowlist)
    if not settings.pilot_closure_enabled:
        return _result(False, selected_mode, "disabled")

    if selected_mode == "livekit_real_media_smoke":
        media = run_webcall_ai_real_media_smoke(settings=settings)
        return _result(media.ok, selected_mode, media.error_code)

    preselected_session = _find_preselected_session(db, settings.pilot_session_public_id)
    gate = evaluate_webcall_ai_pilot_gate(
        session=preselected_session,
        tenant_key=effective_tenant_key,
        settings=settings,
    )
    if not gate.allowed:
        return _result(False, selected_mode, gate.reason)

    if selected_mode == "simulated_full_loop" and settings.tracking_lookup_enabled and not settings.pilot_fake_tracking_enabled:
        if os.getenv("CI", "").strip().lower() in {"1", "true", "yes", "on"}:
            return _result(False, selected_mode, "tracking_unavailable")

    session = resolve_or_create_pilot_voice_session(db, settings=settings, mode=selected_mode)
    if session is None:
        return _result(False, selected_mode, "no_session")

    with _pilot_runtime_overrides(selected_mode, fake_tracking=settings.pilot_fake_tracking_enabled):
        worker_result = run_webcall_ai_worker_once(
            db,
            worker_id,
            limit=1,
            lease_seconds=30,
            session_public_id=session.public_id,
        )

    db.refresh(session)
    evidence = build_webcall_ai_evidence_report(db, session=session) if settings.pilot_evidence_enabled else None
    action_count = db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.voice_session_id == session.id).count()
    handoff_events = (
        db.query(WebchatVoiceAIAction)
        .filter(
            WebchatVoiceAIAction.voice_session_id == session.id,
            WebchatVoiceAIAction.nexus_decision == "handoff",
            WebchatVoiceAIAction.speedaf_tool_name.is_(None),
        )
        .count()
    )
    ok = bool(
        worker_result.get("claimed", 0) >= 1
        and worker_result.get("turns", 0) >= 1
        and action_count >= 1
        and evidence is not None
        and evidence.ok
    )
    if selected_mode == "simulated_full_loop":
        ok = ok and worker_result.get("transcript_segments", 0) >= 1
        ok = ok and worker_result.get("tts_runtime_events", 0) >= 1
        ok = ok and worker_result.get("voice_egress_sent", 0) >= 1
    if selected_mode == "handoff_safety":
        ok = ok and handoff_events >= 1 and worker_result.get("voice_egress_sent", 0) == 0
    return WebCallAIPilotClosureResult(
        ok=ok,
        mode=selected_mode,
        claimed=int(worker_result.get("claimed", 0)),
        transcript_segments=int(worker_result.get("transcript_segments", 0)),
        turns=int(worker_result.get("turns", 0)),
        actions=int(action_count),
        tts_runtime_events=int(worker_result.get("tts_runtime_events", 0)),
        voice_egress_sent=int(worker_result.get("voice_egress_sent", 0)),
        voice_egress_failures=int(worker_result.get("voice_egress_failures", 0)),
        handoff_events=int(handoff_events),
        evidence_report=1 if evidence is not None and evidence.ok else 0,
        error_code=None if ok else "pilot_closure_failed",
    )


def _find_preselected_session(db: Session, public_id: str | None) -> WebchatVoiceSession | None:
    if not public_id:
        return None
    return db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == public_id).first()


def _first_csv(value: str | None) -> str | None:
    if not value:
        return None
    return next((item.strip() for item in value.split(",") if item.strip()), None)


@contextmanager
def _pilot_runtime_overrides(mode: str, *, fake_tracking: bool):
    from . import mock_media_provider, orchestrator

    original_text = mock_media_provider.MOCK_CUSTOMER_TEXT
    original_lookup = orchestrator.lookup_tracking_fact
    env_updates: dict[str, str] = {}
    if mode == "simulated_full_loop":
        mock_media_provider.MOCK_CUSTOMER_TEXT = "Please track CH020000008030 for me."
        if fake_tracking:
            orchestrator.lookup_tracking_fact = lambda **kwargs: fake_tracking_fact_for_pilot(
                kwargs.get("tracking_number")
            )
    elif mode == "handoff_safety":
        mock_media_provider.MOCK_CUSTOMER_TEXT = "Cancel my order immediately."
        env_updates["WEBCALL_AI_VOICE_EGRESS_ENABLED"] = "false"

    old_env = {key: os.environ.get(key) for key in env_updates}
    try:
        for key, value in env_updates.items():
            os.environ[key] = value
        if env_updates:
            get_webcall_ai_settings.cache_clear()
        yield
    finally:
        mock_media_provider.MOCK_CUSTOMER_TEXT = original_text
        orchestrator.lookup_tracking_fact = original_lookup
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_webcall_ai_settings.cache_clear()


def _result(ok: bool, mode: str, error_code: str | None) -> WebCallAIPilotClosureResult:
    return WebCallAIPilotClosureResult(
        ok=ok,
        mode=mode,
        claimed=0,
        transcript_segments=0,
        turns=0,
        actions=0,
        tts_runtime_events=0,
        voice_egress_sent=0,
        voice_egress_failures=0,
        handoff_events=0,
        evidence_report=0,
        error_code=error_code,
    )
