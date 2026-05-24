from __future__ import annotations

import time

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceSession
from .evidence import persist_turn_evidence
from .providers.base import LLMResult, ProviderError, STTResult
from .providers.fake import FakeLLMProvider, FakeSTTProvider, FakeTTSProvider
from .providers.router import get_llm_provider, get_stt_provider, get_tts_provider
from .tool_registry import default_registry
from .tools.tracking_lookup import extract_tracking_number


def run_fake_turn(audio_or_text: bytes | str, *, language: str | None = None) -> dict[str, object]:
    audio = audio_or_text.encode("utf-8") if isinstance(audio_or_text, str) else audio_or_text
    stt = FakeSTTProvider().transcribe(audio, language=language)
    llm = FakeLLMProvider().respond(stt.text, language=stt.language)
    tool_result = None
    tracking_number = extract_tracking_number(stt.text)
    if tracking_number:
        tool_result = default_registry().call("tracking_lookup", {"tracking_number": tracking_number})
    tts = FakeTTSProvider().synthesize(llm.response_text, language=stt.language)
    return {
        "transcript": stt.__dict__,
        "response": llm.__dict__,
        "tool_result": tool_result,
        "tts": {"mime_type": tts.mime_type, "bytes": len(tts.audio_bytes), "text": tts.text, "provider": tts.provider_name, "_audio_bytes": tts.audio_bytes},
    }


def run_session_turn(
    db: Session,
    *,
    session: WebchatVoiceSession,
    audio: bytes,
    worker_id: str,
    language: str | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    mime_type: str | None = None,
) -> dict[str, object]:
    from .config import get_webcall_ai_production_settings

    settings = get_webcall_ai_production_settings()
    started = time.monotonic()
    try:
        stt = get_stt_provider(settings.stt_provider).transcribe(
            audio,
            language=language or session.ai_language,
            sample_rate=sample_rate,
            channels=channels,
            mime_type=mime_type,
        )
    except ProviderError as exc:
        return build_handoff_turn(
            db,
            session=session,
            worker_id=worker_id,
            response_text="I cannot safely transcribe the call right now. I will hand this to a human support agent.",
            intent="stt_provider_failed",
            handoff_required=True,
            handoff_reason=exc.code,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    tracking_number = extract_tracking_number(stt.text)
    tool_result = None
    if tracking_number:
        tool_result = default_registry().call("tracking_lookup", {"tracking_number": tracking_number})
        status = ((tool_result or {}).get("result") or {}).get("status")
        if status == "not_configured":
            llm = LLMResult(
                response_text=((tool_result or {}).get("result") or {}).get("summary") or "Tracking lookup is not configured. I will hand this request to a human support agent.",
                intent="tracking_lookup_not_configured",
                handoff_required=True,
                handoff_reason="tracking_lookup_not_configured",
                provider_name="tool_policy",
            )
        else:
            llm = _safe_llm_response(settings, stt)
    else:
        llm = _safe_llm_response(settings, stt)
    if tracking_number and not llm.handoff_required:
        summary = ((tool_result or {}).get("result") or {}).get("summary")
        if summary:
            llm = type(llm)(
                response_text=f"I checked the approved tracking tool. {summary}",
                intent=llm.intent,
                handoff_required=llm.handoff_required,
                handoff_reason=llm.handoff_reason,
                provider_name=llm.provider_name,
            )
    tts = get_tts_provider(settings.tts_provider).synthesize(llm.response_text, language=stt.language)
    evidence = persist_turn_evidence(
        db,
        session=session,
        stt=stt,
        llm=llm,
        tts=tts,
        tool_result=tool_result,
        tracking_number=tracking_number,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    db.commit()
    return {
        "turn_id": evidence.turn.id,
        "transcript": stt.__dict__,
        "response": llm.__dict__,
        "tool_result": tool_result,
        "tts": {"mime_type": tts.mime_type, "bytes": len(tts.audio_bytes), "text": tts.text, "provider": tts.provider_name, "_audio_bytes": tts.audio_bytes},
        "worker_id": worker_id,
        "handoff_required": llm.handoff_required,
        "handoff_reason": llm.handoff_reason,
    }


def _safe_llm_response(settings, stt: STTResult) -> LLMResult:
    try:
        return get_llm_provider(settings.llm_provider).respond(stt.text, language=stt.language)
    except ProviderError as exc:
        return LLMResult(
            response_text="I cannot safely complete the AI answer right now. I will hand this to a human support agent.",
            intent="llm_provider_failed",
            handoff_required=True,
            handoff_reason=exc.code,
            provider_name="provider_failure",
        )


def build_handoff_turn(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    response_text: str,
    intent: str,
    handoff_required: bool,
    handoff_reason: str | None,
    latency_ms: int | None = None,
) -> dict[str, object]:
    from .config import get_webcall_ai_production_settings

    settings = get_webcall_ai_production_settings()
    stt = STTResult(text="", language=session.ai_language or "en", confidence=None, provider_name="none")
    llm = LLMResult(
        response_text=response_text,
        intent=intent,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        provider_name="worker_policy",
    )
    tts = get_tts_provider(settings.tts_provider).synthesize(llm.response_text, language=stt.language)
    evidence = persist_turn_evidence(
        db,
        session=session,
        stt=stt,
        llm=llm,
        tts=tts,
        tool_result=None,
        tracking_number=None,
        latency_ms=latency_ms,
    )
    db.commit()
    return {
        "turn_id": evidence.turn.id,
        "response": llm.__dict__,
        "tts": {"mime_type": tts.mime_type, "bytes": len(tts.audio_bytes), "text": tts.text, "provider": tts.provider_name, "_audio_bytes": tts.audio_bytes},
        "worker_id": worker_id,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason,
    }
