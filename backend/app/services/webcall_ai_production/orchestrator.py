from __future__ import annotations

import time

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceAITurn, WebchatVoiceSession
from .evidence import persist_turn_evidence
from .metrics import record_webcall_ai_stage
from .providers.base import LLMResult, ProviderError, STTResult, TTSResult
from .providers.cancel_token import CancelToken
from .providers.fake import FakeLLMProvider, FakeSTTProvider, FakeTTSProvider
from .providers.router import get_llm_provider, get_stt_provider, get_tts_provider
from .tool_registry import default_registry
from .tools.tracking_lookup import extract_tracking_number, is_tracking_question


ASK_TRACKING_NUMBER_REPLY = "Please provide your tracking number."
TRACKING_LOOKUP_NOT_CONNECTED_REPLY = "Tracking lookup is not connected yet. I have recorded your tracking number and a human agent will follow up if needed."
EMPTY_TRANSCRIPT_FIRST_REPLY = "Sorry, I didn’t catch that. Could you repeat your question or tracking number?"
EMPTY_TRANSCRIPT_SECOND_REPLY = "Please say the tracking number slowly, or type it if available."
EMPTY_TRANSCRIPT_HANDOFF_REPLY = "I still cannot reliably hear the caller. I will hand this to a human support agent."


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
        "tts": _tts_payload(tts),
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
        stage_started = time.monotonic()
        stt = get_stt_provider(settings.stt_provider).transcribe(
            audio,
            language=language or session.ai_language,
            sample_rate=sample_rate,
            channels=channels,
            mime_type=mime_type,
        )
        record_webcall_ai_stage(stage="stt_final", provider=stt.provider_name, elapsed_ms=int((time.monotonic() - stage_started) * 1000))
    except ProviderError as exc:
        record_webcall_ai_stage(stage="stt_final", status=exc.code, provider=exc.provider, elapsed_ms=int((time.monotonic() - started) * 1000))
        if exc.code == "stt_empty_transcript":
            return _handle_empty_transcript(
                db,
                session=session,
                worker_id=worker_id,
                provider_name=exc.provider,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return build_handoff_turn(
            db,
            session=session,
            worker_id=worker_id,
            response_text="I cannot safely transcribe the call right now. I will hand this to a human support agent.",
            intent="stt_provider_failed",
            handoff_required=True,
            handoff_reason=exc.code,
            latency_ms=int((time.monotonic() - started) * 1000),
            stt_provider=exc.provider,
        )
    tracking_number = extract_tracking_number(stt.text)
    tool_result = None
    tracking_lookup_status = None
    if tracking_number:
        tool_result = default_registry().call("tracking_lookup", {"tracking_number": tracking_number})
        tracking_lookup_status = ((tool_result or {}).get("result") or {}).get("status")
        if tracking_lookup_status == "not_configured":
            llm = LLMResult(
                response_text=((tool_result or {}).get("result") or {}).get("summary") or TRACKING_LOOKUP_NOT_CONNECTED_REPLY,
                intent="tracking_lookup_not_configured",
                handoff_required=False,
                handoff_reason=None,
                provider_name="tool_policy",
            )
        else:
            llm = _timed_llm_response(settings, stt)
    elif is_tracking_question(stt.text):
        llm = LLMResult(
            response_text=ASK_TRACKING_NUMBER_REPLY,
            intent="ask_tracking_number",
            handoff_required=False,
            handoff_reason=None,
            provider_name="tool_policy",
        )
    else:
        llm = _timed_llm_response(settings, stt)
    if tracking_number and not llm.handoff_required and tracking_lookup_status != "not_configured":
        summary = ((tool_result or {}).get("result") or {}).get("summary")
        if summary:
            llm = type(llm)(
                response_text=f"I checked the approved tracking tool. {summary}",
                intent=llm.intent,
                handoff_required=llm.handoff_required,
                handoff_reason=llm.handoff_reason,
                provider_name=llm.provider_name,
            )
    tts_started = time.monotonic()
    tts = _synthesize_tts(settings, llm.response_text, language=stt.language)
    first_chunk_latency = _first_tts_chunk_latency(tts)
    if first_chunk_latency is not None:
        record_webcall_ai_stage(stage="tts_first_audio", provider=tts.provider_name, elapsed_ms=first_chunk_latency)
        record_webcall_ai_stage(stage="end_to_first_audio", provider=tts.provider_name, elapsed_ms=int((time.monotonic() - started) * 1000))
    if tts.audio_stream is None:
        record_webcall_ai_stage(stage="tts_total", provider=tts.provider_name, elapsed_ms=int((time.monotonic() - tts_started) * 1000))
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
        "tts": _tts_payload(tts, turn_started_at=started, tts_started_at=tts_started),
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


def _timed_llm_response(settings, stt: STTResult) -> LLMResult:
    started = time.monotonic()
    llm = _safe_llm_response(settings, stt)
    status = "handoff" if llm.handoff_required else "ok"
    if llm.intent == "llm_provider_failed":
        status = llm.handoff_reason or "llm_provider_failed"
    record_webcall_ai_stage(stage="llm_decision", status=status, provider=llm.provider_name, elapsed_ms=int((time.monotonic() - started) * 1000))
    return llm


def _handle_empty_transcript(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    provider_name: str | None,
    latency_ms: int | None,
) -> dict[str, object]:
    retry_count = _consecutive_empty_transcript_count(db, session=session) + 1
    if retry_count == 1:
        response_text = EMPTY_TRANSCRIPT_FIRST_REPLY
        handoff_required = False
        handoff_reason = None
    elif retry_count == 2:
        response_text = EMPTY_TRANSCRIPT_SECOND_REPLY
        handoff_required = False
        handoff_reason = None
    else:
        response_text = EMPTY_TRANSCRIPT_HANDOFF_REPLY
        handoff_required = True
        handoff_reason = "stt_empty_transcript"
    result = build_handoff_turn(
        db,
        session=session,
        worker_id=worker_id,
        response_text=response_text,
        intent="stt_empty_transcript",
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        latency_ms=latency_ms,
        stt_provider=provider_name or "unknown",
    )
    result["empty_transcript_retry_count"] = retry_count
    return result


def _consecutive_empty_transcript_count(db: Session, *, session: WebchatVoiceSession) -> int:
    rows = (
        db.query(WebchatVoiceAITurn.intent)
        .filter(
            WebchatVoiceAITurn.voice_session_id == session.id,
            WebchatVoiceAITurn.conversation_id == session.conversation_id,
        )
        .order_by(WebchatVoiceAITurn.id.desc())
        .limit(10)
        .all()
    )
    count = 0
    for (intent,) in rows:
        if intent != "stt_empty_transcript":
            break
        count += 1
    return count


def _synthesize_tts(settings, text: str, *, language: str | None) -> TTSResult:
    provider = get_tts_provider(settings.tts_provider)
    lazy_synthesize = getattr(provider, "synthesize_lazy", None)
    if callable(lazy_synthesize):
        return lazy_synthesize(text, language=language, cancel_token=CancelToken())
    return provider.synthesize(text, language=language)


def _first_tts_chunk_latency(tts: TTSResult) -> int | None:
    if not tts.audio_chunks:
        return None
    first = tts.audio_chunks[0]
    value = getattr(first, "provider_latency_ms", None)
    if isinstance(value, int | float):
        return int(value)
    return None


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
    stt_provider: str = "none",
) -> dict[str, object]:
    from .config import get_webcall_ai_production_settings

    settings = get_webcall_ai_production_settings()
    stt = STTResult(text="", language=session.ai_language or "en", confidence=None, provider_name=stt_provider)
    llm = LLMResult(
        response_text=response_text,
        intent=intent,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        provider_name="worker_policy",
    )
    tts_started = time.monotonic()
    tts = _synthesize_tts(settings, llm.response_text, language=stt.language)
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
        "tts": _tts_payload(tts, turn_started_at=tts_started, tts_started_at=tts_started),
        "worker_id": worker_id,
        "handoff_required": handoff_required,
        "handoff_reason": handoff_reason,
    }


def _tts_payload(tts: TTSResult, *, turn_started_at: float | None = None, tts_started_at: float | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "mime_type": tts.mime_type,
        "bytes": len(tts.audio_bytes),
        "text": tts.text,
        "provider": tts.provider_name,
        "_audio_bytes": tts.audio_bytes,
    }
    if turn_started_at is not None:
        payload["_turn_started_at"] = turn_started_at
    if tts_started_at is not None:
        payload["_tts_started_at"] = tts_started_at
    if tts.audio_chunks:
        payload["_audio_chunks"] = tts.audio_chunks
    if tts.audio_stream is not None:
        payload["_audio_stream"] = tts.audio_stream
    if tts.cancel_token is not None:
        payload["_cancel_token"] = tts.cancel_token
    return payload
