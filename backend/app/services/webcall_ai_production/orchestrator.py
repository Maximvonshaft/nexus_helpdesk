from __future__ import annotations

import time

from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceSession
from .evidence import persist_turn_evidence
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
        "tts": {"mime_type": tts.mime_type, "bytes": len(tts.audio_bytes), "text": tts.text, "_audio_bytes": tts.audio_bytes},
    }


def run_session_turn(
    db: Session,
    *,
    session: WebchatVoiceSession,
    audio: bytes,
    worker_id: str,
    language: str | None = None,
) -> dict[str, object]:
    from .config import get_webcall_ai_production_settings

    settings = get_webcall_ai_production_settings()
    started = time.monotonic()
    stt = get_stt_provider(settings.stt_provider).transcribe(audio, language=language or session.ai_language)
    tracking_number = extract_tracking_number(stt.text)
    tool_result = None
    if tracking_number:
        tool_result = default_registry().call("tracking_lookup", {"tracking_number": tracking_number})
    llm = get_llm_provider(settings.llm_provider).respond(stt.text, language=stt.language)
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
        "tts": {"mime_type": tts.mime_type, "bytes": len(tts.audio_bytes), "text": tts.text, "_audio_bytes": tts.audio_bytes},
        "worker_id": worker_id,
    }
