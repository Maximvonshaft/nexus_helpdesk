from __future__ import annotations

import asyncio
import hmac
import json
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from voice_policy import FALLBACK_REPROMPT_EN, SUPPORTED_TTS_LANGUAGES, normalize_language, split_tts_chunks, transcript_quality_reason


WORK_DIR = Path(os.getenv("LIVE_VOICE_WORK_DIR", "/data/ai-runtime/services/nexus_live_voice_media"))
INPUT_DIR = WORK_DIR / "input"
INPUT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = Path(os.getenv("LIVE_VOICE_SHARED_TOKEN_FILE", "/run/nexus/live_voice_token"))
VOICE_URL = os.getenv("LIVE_VOICE_API_URL", "http://127.0.0.1:8010").rstrip("/")
NEXUS_TURN_URL = os.getenv("NEXUS_LIVE_VOICE_TURN_URL", "").strip()
TTS_MAX_CHARS = max(40, min(int(os.getenv("LIVE_VOICE_TTS_MAX_CHARS", "180")), 300))

SAMPLE_RATE_IN = 16000
app = FastAPI(title="Nexus Live Voice Media Edge", version="2026-07-16")


def read_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


async def send_json(ws: WebSocket, lock: asyncio.Lock, obj: dict) -> None:
    async with lock:
        await ws.send_text(json.dumps(obj, ensure_ascii=False))


async def send_binary(ws: WebSocket, lock: asyncio.Lock, data: bytes) -> None:
    async with lock:
        await ws.send_bytes(data)


def write_wav_pcm16(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE_IN) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


def wav_file_to_pcm16_bytes(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width == 2:
        audio = np.frombuffer(frames, dtype=np.int16)
    elif sample_width == 1:
        audio8 = np.frombuffer(frames, dtype=np.uint8).astype(np.int16)
        audio = ((audio8 - 128) << 8).astype(np.int16)
    elif sample_width == 4:
        audio32 = np.frombuffer(frames, dtype=np.int32)
        audio = (audio32 / 65536).clip(-32768, 32767).astype(np.int16)
    else:
        raise RuntimeError("unsupported_tts_sample_width")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return sample_rate, audio.tobytes()


def rms_pcm16(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    audio = np.frombuffer(pcm, dtype=np.int16)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))


def stt_sync(wav_path: Path) -> dict:
    with wav_path.open("rb") as audio:
        response = requests.post(
            f"{VOICE_URL}/stt",
            files={"file": (wav_path.name, audio, "audio/wav")},
            data={"language": "auto", "beam_size": "1"},
            timeout=180,
        )
    response.raise_for_status()
    return response.json()


def orchestrate_sync(
    *,
    conversation_id: str,
    voice_session_id: str,
    turn_id: int,
    transcript: str,
    stt_language: str | None,
) -> dict:
    token = read_token()
    if not token or not NEXUS_TURN_URL:
        raise RuntimeError("nexus_voice_orchestrator_not_configured")
    response = requests.post(
        NEXUS_TURN_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "conversation_id": conversation_id,
            "voice_session_id": voice_session_id,
            "turn_id": turn_id,
            "transcript": transcript,
            "stt_language": stt_language,
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()


def tts_sync(answer: str, language: str | None, speed: float) -> dict:
    normalized_language = normalize_language(language)
    if normalized_language not in SUPPORTED_TTS_LANGUAGES:
        raise RuntimeError("tts_language_not_supported")
    response = requests.post(
        f"{VOICE_URL}/tts",
        json={"text": answer, "language": normalized_language, "speed": speed},
        timeout=300,
    )
    response.raise_for_status()
    result = response.json()
    result["language"] = normalized_language
    return result


@app.get("/health")
def health() -> JSONResponse:
    checks: dict[str, object] = {
        "nexus_orchestrator": {"ok": bool(NEXUS_TURN_URL and read_token())},
    }
    for name, url in (("voice_models", f"{VOICE_URL}/health"),):
        try:
            response = requests.get(url, timeout=3)
            checks[name] = {"ok": response.ok, "data": response.json() if response.text else None}
        except Exception as exc:
            checks[name] = {"ok": False, "error_type": type(exc).__name__}
    healthy = all(bool(item.get("ok")) for item in checks.values() if isinstance(item, dict))
    return JSONResponse(
        {
            "status": "ok" if healthy else "unavailable",
            "service": "nexus-live-voice-media-edge",
            "transport": "websocket-pcm16",
            "features": ["server-vad", "stt-quality-gate", "nexus-runtime-orchestration", "chunked-tts", "echo-safe-turn-taking"],
            "checks": checks,
        },
        status_code=200 if healthy else 503,
    )


class LiveSession:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.send_lock = asyncio.Lock()
        self.conversation_id = str(ws.query_params.get("conversation_id") or "").strip()
        self.voice_session_id = str(ws.query_params.get("voice_session_id") or "").strip()
        self.preferred_language = str(ws.query_params.get("lang_code") or "auto").strip()
        try:
            self.speed = max(0.7, min(1.3, float(ws.query_params.get("speed") or "1.0")))
        except (TypeError, ValueError):
            self.speed = 1.0

        self.turn_id = 0
        self.processing_task: Optional[asyncio.Task] = None
        self.in_speech = False
        self.frames: list[bytes] = []
        self.speech_frames = 0
        self.silence_frames = 0
        self.rms_threshold = 0.018
        self.min_speech_frames = 8
        self.end_silence_frames = 35
        self.ignore_audio_until = 0.0

    async def handle_chunk(self, chunk: bytes) -> None:
        if asyncio.get_running_loop().time() < self.ignore_audio_until:
            self._reset_capture()
            return

        rms = rms_pcm16(chunk)
        if rms >= self.rms_threshold:
            if not self.in_speech:
                self.in_speech = True
                self.frames = []
                self.speech_frames = 0
                self.silence_frames = 0
                await send_json(self.ws, self.send_lock, {"type": "speech_start", "rms": round(rms, 4)})
            self.speech_frames += 1
            self.silence_frames = 0
            self.frames.append(chunk)
            return

        if not self.in_speech:
            return
        self.frames.append(chunk)
        self.silence_frames += 1
        if self.silence_frames < self.end_silence_frames:
            return

        pcm = b"".join(self.frames)
        duration = len(pcm) / 2 / SAMPLE_RATE_IN
        speech_frames = self.speech_frames
        self._reset_capture()
        if speech_frames < self.min_speech_frames or duration < 0.55:
            await send_json(self.ws, self.send_lock, {"type": "speech_ignored", "reason": "too_short"})
            return
        if self.processing_task and not self.processing_task.done():
            await send_json(self.ws, self.send_lock, {"type": "speech_ignored", "reason": "turn_in_progress"})
            return
        self.turn_id += 1
        self.processing_task = asyncio.create_task(self.process_turn(self.turn_id, pcm))

    def _reset_capture(self) -> None:
        self.in_speech = False
        self.frames = []
        self.speech_frames = 0
        self.silence_frames = 0

    async def _stream_tts(
        self,
        *,
        turn_id: int,
        answer: str,
        response_language: str,
        started: float,
        feedback: bool = False,
    ) -> None:
        """Send the first synthesized clause immediately, then preserve PCM order."""
        normalized_language = normalize_language(response_language)
        chunks = split_tts_chunks(answer, max_chars=TTS_MAX_CHARS)
        if normalized_language not in SUPPORTED_TTS_LANGUAGES or not chunks:
            raise RuntimeError("tts_language_not_supported")

        loop = asyncio.get_running_loop()
        tts_started = time.monotonic()
        total_audio_bytes = 0
        total_duration_seconds = 0.0
        sample_rate: int | None = None
        first_audio_ms: int | None = None
        for index, chunk in enumerate(chunks):
            chunk_started = time.monotonic()
            tts = await loop.run_in_executor(None, tts_sync, chunk, normalized_language, self.speed)
            chunk_elapsed_ms = int((time.monotonic() - chunk_started) * 1000)
            path = Path(str(tts.get("path") or ""))
            if not path.is_file():
                raise RuntimeError("tts_file_not_found")
            try:
                chunk_sample_rate, pcm_bytes = wav_file_to_pcm16_bytes(path)
            finally:
                path.unlink(missing_ok=True)
            if sample_rate is None:
                sample_rate = chunk_sample_rate
            elif sample_rate != chunk_sample_rate:
                raise RuntimeError("tts_sample_rate_changed")

            duration_seconds = len(pcm_bytes) / 2 / chunk_sample_rate
            total_audio_bytes += len(pcm_bytes)
            total_duration_seconds += duration_seconds
            self.ignore_audio_until = max(self.ignore_audio_until, loop.time()) + duration_seconds + 0.35
            chunk_bytes = max(2, int(chunk_sample_rate * 2 * 0.1))
            if index == 0:
                first_audio_ms = int((time.monotonic() - tts_started) * 1000)
                await send_json(
                    self.ws,
                    self.send_lock,
                    {
                        "type": "tts_start",
                        "turn_id": turn_id,
                        "sample_rate": chunk_sample_rate,
                        "engine": tts.get("engine"),
                        "language": normalized_language,
                        "audio_format": "pcm16le",
                        "chunked": len(chunks) > 1,
                        "first_audio_ms": first_audio_ms,
                        "feedback": feedback,
                    },
                )
            await send_json(
                self.ws,
                self.send_lock,
                {
                    "type": "tts_chunk",
                    "turn_id": turn_id,
                    "index": index,
                    "count": len(chunks),
                    "text_chars": len(chunk),
                    "tts_elapsed_ms": chunk_elapsed_ms,
                },
            )
            for offset in range(0, len(pcm_bytes), chunk_bytes):
                await send_binary(self.ws, self.send_lock, pcm_bytes[offset : offset + chunk_bytes])

        await send_json(
            self.ws,
            self.send_lock,
            {
                "type": "tts_end",
                "turn_id": turn_id,
                "bytes": total_audio_bytes,
                "chunks": len(chunks),
                "duration_ms": round(total_duration_seconds * 1000),
                "tts_elapsed_ms": int((time.monotonic() - tts_started) * 1000),
                "first_audio_ms": first_audio_ms,
                "total_elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )

    async def process_turn(self, turn_id: int, pcm: bytes) -> None:
        descriptor, raw_path = tempfile.mkstemp(
            prefix="turn-", suffix=".wav", dir=INPUT_DIR
        )
        os.close(descriptor)
        wav_path = Path(raw_path)
        started = time.monotonic()
        try:
            write_wav_pcm16(wav_path, pcm)
            await send_json(
                self.ws,
                self.send_lock,
                {"type": "speech_end", "turn_id": turn_id, "duration_sec": round(len(pcm) / 2 / SAMPLE_RATE_IN, 2)},
            )
            await send_json(self.ws, self.send_lock, {"type": "stt_start", "turn_id": turn_id})
            loop = asyncio.get_running_loop()
            stt_started = time.monotonic()
            stt = await loop.run_in_executor(None, stt_sync, wav_path)
            stt_elapsed_ms = int((time.monotonic() - stt_started) * 1000)
            transcript = str(stt.get("text") or "").strip()
            stt_language = normalize_language(str(stt.get("language") or "").strip()) or None
            quality_reason = transcript_quality_reason(transcript, stt_language)
            await send_json(
                self.ws,
                self.send_lock,
                {
                    "type": "stt_result",
                    "turn_id": turn_id,
                    "text": transcript,
                    "language": stt_language,
                    "elapsed_ms": stt_elapsed_ms,
                },
            )
            if quality_reason:
                await send_json(
                    self.ws,
                    self.send_lock,
                    {"type": "stt_rejected", "turn_id": turn_id, "reason": quality_reason},
                )
                await send_json(
                    self.ws,
                    self.send_lock,
                    {"type": "ai_answer", "turn_id": turn_id, "answer": FALLBACK_REPROMPT_EN, "language": "en", "fallback": True},
                )
                await self._stream_tts(
                    turn_id=turn_id,
                    answer=FALLBACK_REPROMPT_EN,
                    response_language="en",
                    started=started,
                    feedback=True,
                )
                return
            if stt_language not in SUPPORTED_TTS_LANGUAGES:
                await send_json(
                    self.ws,
                    self.send_lock,
                    {"type": "stt_rejected", "turn_id": turn_id, "reason": "language_not_supported"},
                )
                await send_json(
                    self.ws,
                    self.send_lock,
                    {"type": "ai_answer", "turn_id": turn_id, "answer": FALLBACK_REPROMPT_EN, "language": "en", "fallback": True},
                )
                await self._stream_tts(
                    turn_id=turn_id,
                    answer=FALLBACK_REPROMPT_EN,
                    response_language="en",
                    started=started,
                    feedback=True,
                )
                return
            if not transcript:
                await send_json(self.ws, self.send_lock, {"type": "turn_complete", "turn_id": turn_id, "reply": None})
                return

            await send_json(self.ws, self.send_lock, {"type": "thinking_start", "turn_id": turn_id})
            runtime_started = time.monotonic()
            result = await loop.run_in_executor(
                None,
                lambda: orchestrate_sync(
                    conversation_id=self.conversation_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=turn_id,
                    transcript=transcript,
                    stt_language=stt_language,
                ),
            )
            runtime_elapsed_ms = int((time.monotonic() - runtime_started) * 1000)
            answer = str(result.get("reply") or "").strip()
            if not answer:
                await send_json(
                    self.ws,
                    self.send_lock,
                    {"type": "turn_complete", "turn_id": turn_id, "reply": None, "status": result.get("status")},
                )
                return

            response_language = str(result.get("language") or stt_language or self.preferred_language).strip()
            await send_json(
                self.ws,
                self.send_lock,
                {
                    "type": "ai_answer",
                    "turn_id": turn_id,
                    "answer": answer,
                    "language": response_language,
                    "reply_source": result.get("reply_source"),
                    "runtime_elapsed_ms": runtime_elapsed_ms,
                },
            )

            await self._stream_tts(
                turn_id=turn_id,
                answer=answer,
                response_language=response_language,
                started=started,
            )
        except asyncio.CancelledError:
            await send_json(self.ws, self.send_lock, {"type": "turn_cancelled", "turn_id": turn_id})
        except Exception as exc:
            await send_json(
                self.ws,
                self.send_lock,
                {"type": "turn_error", "turn_id": turn_id, "error": type(exc).__name__},
            )
        finally:
            wav_path.unlink(missing_ok=True)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    supplied_token = str(ws.query_params.get("token") or "")
    expected_token = read_token()
    conversation_id = str(ws.query_params.get("conversation_id") or "").strip()
    voice_session_id = str(ws.query_params.get("voice_session_id") or "").strip()
    if (
        not expected_token
        or not hmac.compare_digest(supplied_token, expected_token)
        or not conversation_id
        or not voice_session_id
    ):
        await ws.close(code=1008)
        return

    await ws.accept()
    session = LiveSession(ws)
    await send_json(
        ws,
        session.send_lock,
        {
            "type": "connected",
            "sample_rate_in": SAMPLE_RATE_IN,
            "features": ["vad", "stt", "nexus-runtime-orchestration", "tts"],
            "voice_session_id": voice_session_id,
        },
    )
    try:
        while True:
            message = await ws.receive()
            if message.get("bytes") is not None:
                await session.handle_chunk(message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        if session.processing_task and not session.processing_task.done():
            session.processing_task.cancel()
