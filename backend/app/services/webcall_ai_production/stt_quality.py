from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from ...utils.time import ensure_utc, format_utc, utc_now
from ...voice_models import WebchatVoiceAITurn, WebchatVoiceSession
from ...webchat_models import WebchatEvent
from .event_service import write_event
from .evidence import redact_customer_text
from .providers.base import ProviderError, STTResult
from .providers.deepgram_streaming_stt import DeepgramStreamingSTTProvider, deepgram_query, prepare_streaming_pcm16


@dataclass(frozen=True)
class PreparedSTTInput:
    audio: bytes
    sample_rate: int | None
    channels: int | None
    mime_type: str | None
    request_contract: dict[str, Any] | None
    diagnostics: dict[str, Any]


def prepare_stt_input(
    settings,
    *,
    session: WebchatVoiceSession,
    audio: bytes,
    language: str | None,
    sample_rate: int | None,
    channels: int | None,
    mime_type: str | None,
    audio_stats: dict[str, object] | None,
    turn_index: int,
) -> PreparedSTTInput:
    if getattr(settings, "stt_provider", None) != "deepgram_streaming":
        return PreparedSTTInput(audio=audio, sample_rate=sample_rate, channels=channels, mime_type=mime_type, request_contract=None, diagnostics={})

    try:
        pcm, resolved_sample_rate, resolved_channels = prepare_streaming_pcm16(
            audio,
            sample_rate=sample_rate,
            channels=channels,
            mime_type=mime_type,
        )
    except ProviderError as exc:
        contract = _deepgram_contract_payload(
            settings,
            session=session,
            turn_index=turn_index,
            language=language or session.ai_language,
            sample_rate=sample_rate,
            channels=channels,
            pcm=audio,
            audio_stats=audio_stats,
            mismatch_reasons=[exc.code],
        )
        return PreparedSTTInput(audio=audio, sample_rate=sample_rate, channels=channels, mime_type=mime_type, request_contract=contract, diagnostics={})

    diagnostics = _pcm_diagnostics(pcm, sample_rate=resolved_sample_rate, channels=resolved_channels, audio_stats=audio_stats, low_rms_threshold=int(settings.stt_low_rms_threshold))
    normalized_pcm, normalization = _maybe_normalize_pcm(pcm, diagnostics=diagnostics, enabled=bool(settings.stt_normalize_pcm_enabled))
    diagnostics.update(normalization)
    contract = _deepgram_contract_payload(
        settings,
        session=session,
        turn_index=turn_index,
        language=language or session.ai_language,
        sample_rate=resolved_sample_rate,
        channels=resolved_channels,
        pcm=normalized_pcm,
        audio_stats={**(audio_stats or {}), **diagnostics},
        mismatch_reasons=[],
    )
    return PreparedSTTInput(
        audio=normalized_pcm,
        sample_rate=resolved_sample_rate,
        channels=resolved_channels,
        mime_type="audio/pcm",
        request_contract=contract,
        diagnostics=diagnostics,
    )


def write_stt_request_contract_event(db: Session, *, session: WebchatVoiceSession, contract: dict[str, Any] | None) -> None:
    if not contract:
        return
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.stt.request_contract",
        payload=_safe_stt_payload(contract),
    )
    db.flush()


def run_deepgram_shadow_canary(
    db: Session,
    settings,
    *,
    session: WebchatVoiceSession,
    prepared: PreparedSTTInput,
    language: str | None,
    audio_stats: dict[str, object] | None,
    turn_index: int,
) -> None:
    if getattr(settings, "stt_provider", None) != "deepgram_streaming" or not bool(getattr(settings, "stt_shadow_canary_enabled", False)):
        return
    if not prepared.audio or not prepared.sample_rate or not prepared.channels:
        return

    results: list[dict[str, Any]] = []
    for candidate in _shadow_candidates(language=language or session.ai_language, sample_rate=prepared.sample_rate, channels=prepared.channels):
        contract = _deepgram_contract_payload(
            settings,
            session=session,
            turn_index=turn_index,
            language=language or session.ai_language,
            sample_rate=prepared.sample_rate,
            channels=prepared.channels,
            pcm=prepared.audio,
            audio_stats={**(audio_stats or {}), **prepared.diagnostics},
            mismatch_reasons=[],
            overrides=candidate["overrides"],
            shadow_candidate=str(candidate["name"]),
        )
        write_stt_request_contract_event(db, session=session, contract=contract)
        started = time.monotonic()
        transcript = ""
        confidence: int | None = None
        error_code: str | None = None
        ok = False
        try:
            result = DeepgramStreamingSTTProvider(
                endpoint=os.getenv("STT_ENDPOINT"),
                token_file=os.getenv("STT_API_KEY_FILE"),
                request_overrides=candidate["overrides"],
            ).transcribe(
                prepared.audio,
                language=language or session.ai_language,
                sample_rate=prepared.sample_rate,
                channels=prepared.channels,
                mime_type=prepared.mime_type,
            )
            transcript = result.text
            confidence = result.confidence
            ok = True
        except ProviderError as exc:
            error_code = exc.code
        except Exception as exc:  # pragma: no cover - defensive evidence path
            error_code = type(exc).__name__
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = _safe_stt_payload(
            {
                "voice_session_id": session.public_id,
                "turn_index": turn_index,
                "stt_provider": "deepgram_streaming",
                "shadow_candidate": str(candidate["name"]),
                "ok": ok,
                "transcript_redacted": redact_customer_text(transcript),
                "confidence": confidence,
                "provider_latency_ms": elapsed_ms,
                "input_audio_ms": contract.get("input_audio_ms"),
                "request_contract": contract,
                "error_code": error_code,
            }
        )
        write_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="webcall_ai.stt.shadow_result", payload=payload)
        results.append(payload)
    winner = _shadow_winner(results)
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.stt.shadow_winner",
        payload=_safe_stt_payload(
            {
                "voice_session_id": session.public_id,
                "turn_index": turn_index,
                "stt_provider": "deepgram_streaming",
                "shadow_candidate": winner.get("shadow_candidate") if winner else None,
                "confidence": winner.get("confidence") if winner else None,
                "transcript_redacted": winner.get("transcript_redacted") if winner else None,
                "provider_latency_ms": winner.get("provider_latency_ms") if winner else None,
                "ok": bool(winner),
            }
        ),
    )
    db.flush()


def write_possible_tts_echo_event(db: Session, *, session: WebchatVoiceSession, stt: STTResult, turn_index: int) -> None:
    transcript = (stt.text or "").strip()
    if not transcript:
        return
    last_turn = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(WebchatVoiceAITurn.id.desc())
        .first()
    )
    if last_turn is None or not (last_turn.ai_response_text_redacted or "").strip():
        return
    similarity = _text_similarity(transcript, last_turn.ai_response_text_redacted or "")
    threshold = _float_env("WEBCALL_AI_STT_ECHO_SIMILARITY_THRESHOLD", 0.82, minimum=0.1, maximum=1.0)
    if similarity < threshold:
        return
    spoken_event = _latest_event(db, session=session, event_type="webcall_ai.response.spoken")
    listening_event = _latest_event(db, session=session, event_type="webcall_ai.agent.listening")
    max_age_ms = _int_env("WEBCALL_AI_STT_ECHO_MAX_AGE_MS", 10000, minimum=0, maximum=60000)
    age_ms = _age_ms(spoken_event.created_at if spoken_event is not None else None)
    if age_ms is not None and age_ms > max_age_ms:
        return
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.stt.possible_tts_echo",
        payload=_safe_stt_payload(
            {
                "voice_session_id": session.public_id,
                "turn_index": turn_index,
                "last_ai_spoken_age_ms": age_ms,
                "similarity_to_last_ai_response": round(similarity, 4),
                "listen_started_at": _isoformat(listening_event.created_at if listening_event is not None else None),
                "tts_finished_at": _isoformat(spoken_event.created_at if spoken_event is not None else None),
            }
        ),
    )
    db.flush()


def _deepgram_contract_payload(
    settings,
    *,
    session: WebchatVoiceSession,
    turn_index: int,
    language: str | None,
    sample_rate: int | None,
    channels: int | None,
    pcm: bytes,
    audio_stats: dict[str, object] | None,
    mismatch_reasons: list[str],
    overrides: dict[str, str] | None = None,
    shadow_candidate: str | None = None,
) -> dict[str, Any]:
    resolved_sample_rate = int(sample_rate or 0)
    resolved_channels = int(channels or 0)
    query = deepgram_query(language=language, sample_rate=resolved_sample_rate, channels=resolved_channels, overrides=overrides) if resolved_sample_rate and resolved_channels else {}
    request_sample_rate = _int_or_none(query.get("sample_rate"))
    request_channels = _int_or_none(query.get("channels"))
    request_encoding = str(query.get("encoding") or "")
    reasons = list(mismatch_reasons)
    if request_encoding.lower() != "linear16":
        reasons.append("request_encoding_not_linear16")
    if not request_sample_rate or request_sample_rate != resolved_sample_rate:
        reasons.append("request_sample_rate_mismatch")
    if not request_channels or request_channels != resolved_channels:
        reasons.append("request_channels_mismatch")
    diagnostics = _pcm_diagnostics(pcm, sample_rate=resolved_sample_rate, channels=resolved_channels, audio_stats=audio_stats, low_rms_threshold=int(settings.stt_low_rms_threshold)) if resolved_sample_rate and resolved_channels else {}
    for key in ["normalization_applied", "gain_db", "normalization_clipping_ratio"]:
        if audio_stats and key in audio_stats:
            diagnostics[key] = audio_stats[key]
    return {
        "voice_session_id": session.public_id,
        "turn_index": turn_index,
        "stt_provider": "deepgram_streaming",
        "shadow_candidate": shadow_candidate,
        "request_model": query.get("model"),
        "request_language": query.get("language") or (language or None),
        "request_encoding": request_encoding or None,
        "request_sample_rate": request_sample_rate,
        "request_channels": request_channels,
        "request_endpointing": _int_or_string(query.get("endpointing")),
        "utterance_end_ms": _int_or_none(query.get("utterance_end_ms")),
        "vad_events": _bool_or_string(query.get("vad_events")),
        "interim_results": _bool_or_string(query.get("interim_results")),
        "smart_format": _bool_or_string(query.get("smart_format")),
        "punctuate": _bool_or_string(query.get("punctuate")),
        "input_pcm_sample_rate": resolved_sample_rate or None,
        "input_pcm_channels": resolved_channels or None,
        "input_pcm_bytes": len(pcm or b""),
        "input_audio_ms": _audio_ms(len(pcm or b""), sample_rate=resolved_sample_rate, channels=resolved_channels),
        "input_rms_min": diagnostics.get("input_rms_min"),
        "input_rms_avg": diagnostics.get("input_rms_avg"),
        "input_rms_max": diagnostics.get("input_rms_max"),
        "input_dbfs": diagnostics.get("input_dbfs"),
        "rms_dynamic_range": diagnostics.get("rms_dynamic_range"),
        "peak_clipping_ratio": diagnostics.get("peak_clipping_ratio"),
        "zero_crossing_rate": diagnostics.get("zero_crossing_rate"),
        "low_input_level": diagnostics.get("low_input_level"),
        "normalization_applied": diagnostics.get("normalization_applied", False),
        "gain_db": diagnostics.get("gain_db"),
        "normalization_clipping_ratio": diagnostics.get("normalization_clipping_ratio"),
        "contract_match": not reasons,
        "mismatch_reasons": sorted(set(reasons)),
    }


def _pcm_diagnostics(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    audio_stats: dict[str, object] | None,
    low_rms_threshold: int,
) -> dict[str, Any]:
    sample_count = max(0, len(pcm or b"") // 2)
    if sample_count <= 0:
        rms_whole = 0
        peak_abs = 0
        clipping_ratio = 0.0
        zero_crossing_rate = 0.0
    else:
        square_sum = 0
        clipping_count = 0
        zero_crossings = 0
        previous_sign: int | None = None
        peak_abs = 0
        for offset in range(0, len(pcm) - 1, 2):
            sample = int.from_bytes(pcm[offset : offset + 2], byteorder="little", signed=True)
            absolute = abs(sample)
            peak_abs = max(peak_abs, absolute)
            square_sum += sample * sample
            if absolute >= 32760:
                clipping_count += 1
            sign = 1 if sample >= 0 else -1
            if previous_sign is not None and sign != previous_sign:
                zero_crossings += 1
            previous_sign = sign
        rms_whole = int(math.sqrt(square_sum / sample_count))
        clipping_ratio = round(clipping_count / sample_count, 6)
        zero_crossing_rate = round(zero_crossings / max(1, sample_count - 1), 6)
    stats = audio_stats or {}
    input_rms_min = _int_or_none(stats.get("rms_min")) if "rms_min" in stats else rms_whole
    input_rms_avg = _int_or_none(stats.get("rms_avg")) if "rms_avg" in stats else rms_whole
    input_rms_max = _int_or_none(stats.get("rms_max")) if "rms_max" in stats else peak_abs
    input_rms_avg = int(input_rms_avg or 0)
    return {
        "input_rms_min": int(input_rms_min or 0),
        "input_rms_avg": input_rms_avg,
        "input_rms_max": int(input_rms_max or 0),
        "input_dbfs": _dbfs(input_rms_avg),
        "rms_dynamic_range": max(0, int(input_rms_max or 0) - int(input_rms_min or 0)),
        "peak_clipping_ratio": clipping_ratio,
        "zero_crossing_rate": zero_crossing_rate,
        "low_input_level": bool(input_rms_avg < low_rms_threshold),
        "input_pcm_sample_rate": sample_rate,
        "input_pcm_channels": channels,
    }


def _maybe_normalize_pcm(pcm: bytes, *, diagnostics: dict[str, Any], enabled: bool) -> tuple[bytes, dict[str, Any]]:
    if not enabled:
        return pcm, {"normalization_applied": False, "gain_db": 0.0, "normalization_clipping_ratio": diagnostics.get("peak_clipping_ratio", 0.0)}
    rms_avg = int(diagnostics.get("input_rms_avg") or 0)
    if rms_avg <= 0:
        return pcm, {"normalization_applied": False, "gain_db": 0.0, "normalization_clipping_ratio": diagnostics.get("peak_clipping_ratio", 0.0)}
    target_rms = _int_env("WEBCALL_AI_STT_NORMALIZE_TARGET_RMS", 2600, minimum=100, maximum=12000)
    max_gain_db = _float_env("WEBCALL_AI_STT_NORMALIZE_MAX_GAIN_DB", 12.0, minimum=0.0, maximum=30.0)
    gain = min(target_rms / rms_avg, math.pow(10, max_gain_db / 20.0))
    if gain <= 1.01:
        return pcm, {"normalization_applied": False, "gain_db": 0.0, "normalization_clipping_ratio": diagnostics.get("peak_clipping_ratio", 0.0)}
    output = bytearray()
    clipped = 0
    sample_count = max(1, len(pcm) // 2)
    for offset in range(0, len(pcm) - 1, 2):
        sample = int.from_bytes(pcm[offset : offset + 2], byteorder="little", signed=True)
        amplified = int(round(sample * gain))
        if amplified > 32767:
            amplified = 32767
            clipped += 1
        elif amplified < -32768:
            amplified = -32768
            clipped += 1
        output.extend(amplified.to_bytes(2, "little", signed=True))
    return bytes(output), {
        "normalization_applied": True,
        "gain_db": round(20 * math.log10(gain), 2),
        "normalization_clipping_ratio": round(clipped / sample_count, 6),
    }


def _shadow_candidates(*, language: str | None, sample_rate: int, channels: int) -> list[dict[str, Any]]:
    resolved_language = (language or os.getenv("STT_LANGUAGE") or "en").strip() or "en"
    common = {
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
        "channels": str(channels),
        "language": resolved_language,
        "interim_results": "true",
        "vad_events": "true",
        "punctuate": "true",
    }
    return [
        {"name": "current_production_config", "overrides": {}},
        {"name": "explicit_48k_linear16_en", "overrides": {**common, "smart_format": "false"}},
        {"name": "explicit_48k_linear16_en_with_smart_format", "overrides": {**common, "smart_format": "true"}},
    ]


def _shadow_winner(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    successful = [item for item in results if item.get("ok") and item.get("transcript_redacted")]
    if not successful:
        return None
    return max(successful, key=lambda item: (int(item.get("confidence") or 0), len(str(item.get("transcript_redacted") or ""))))


def _safe_stt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "voice_session_id",
        "turn_index",
        "stt_provider",
        "shadow_candidate",
        "request_model",
        "request_language",
        "request_encoding",
        "request_sample_rate",
        "request_channels",
        "request_endpointing",
        "utterance_end_ms",
        "vad_events",
        "interim_results",
        "smart_format",
        "punctuate",
        "input_pcm_sample_rate",
        "input_pcm_channels",
        "input_pcm_bytes",
        "input_audio_ms",
        "input_rms_min",
        "input_rms_avg",
        "input_rms_max",
        "input_dbfs",
        "rms_dynamic_range",
        "peak_clipping_ratio",
        "zero_crossing_rate",
        "low_input_level",
        "normalization_applied",
        "gain_db",
        "normalization_clipping_ratio",
        "contract_match",
        "mismatch_reasons",
        "ok",
        "transcript_redacted",
        "confidence",
        "provider_latency_ms",
        "request_contract",
        "error_code",
        "last_ai_spoken_age_ms",
        "similarity_to_last_ai_response",
        "listen_started_at",
        "tts_finished_at",
    }
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if isinstance(value, dict):
            sanitized[key] = _safe_stt_payload(value)
        elif isinstance(value, list):
            sanitized[key] = [str(item)[:160] for item in value[:10]]
        elif isinstance(value, str):
            sanitized[key] = (redact_customer_text(value) if key == "transcript_redacted" else value)[:300]
        elif isinstance(value, bool) or isinstance(value, int) or value is None:
            sanitized[key] = value
        elif isinstance(value, float):
            sanitized[key] = round(value, 6)
    return sanitized


def _latest_event(db: Session, *, session: WebchatVoiceSession, event_type: str) -> WebchatEvent | None:
    return (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == session.conversation_id, WebchatEvent.event_type == event_type)
        .order_by(WebchatEvent.id.desc())
        .first()
    )


def _text_similarity(left: str, right: str) -> float:
    normalized_left = " ".join((left or "").lower().strip().split())
    normalized_right = " ".join((right or "").lower().strip().split())
    if not normalized_left or not normalized_right:
        return 0.0
    if len(normalized_left) <= 12 and normalized_left in normalized_right:
        return 1.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _age_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    value_utc = ensure_utc(value)
    return max(0, int((utc_now() - value_utc).total_seconds() * 1000)) if value_utc is not None else None


def _isoformat(value: datetime | None) -> str | None:
    return format_utc(value)


def _audio_ms(pcm_bytes: int, *, sample_rate: int, channels: int) -> int:
    if sample_rate <= 0 or channels <= 0 or pcm_bytes <= 0:
        return 0
    return int(((pcm_bytes / 2 / channels) / sample_rate) * 1000)


def _dbfs(rms: int) -> float:
    if rms <= 0:
        return -120.0
    return round(20 * math.log10(min(32768, rms) / 32768), 2)


def _bool_or_string(value: Any) -> bool | str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    return str(value)[:80]


def _int_or_string(value: Any) -> int | str | None:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else (str(value)[:80] if value is not None else None)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    parsed = _int_or_none(os.getenv(name))
    return max(minimum, min(parsed if parsed is not None else default, maximum))


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
