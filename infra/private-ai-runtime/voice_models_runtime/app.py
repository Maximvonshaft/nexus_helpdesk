from __future__ import annotations

import asyncio
import inspect
import os
import re
import tempfile
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field


OUTPUT_DIR = Path(os.getenv("VOICE_MODEL_OUTPUT_DIR", "/var/lib/nexus/voice-models/output"))
ASR_MODEL_PATH = Path(os.getenv("NEMOTRON_ASR_MODEL_PATH", "")).expanduser()
TTS_MODEL_PATH = Path(os.getenv("QWEN3_TTS_MODEL_PATH", "")).expanduser()
TTS_SPEAKER = os.getenv("QWEN3_TTS_SPEAKER", "Ryan").strip()
ATTN_IMPLEMENTATION = os.getenv("QWEN3_TTS_ATTN_IMPLEMENTATION", "sdpa").strip()
SUPPORTED_LANGUAGES = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
}


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=8)
    speed: float = Field(default=1.0, ge=0.7, le=1.3)


class VoiceModels:
    def __init__(self) -> None:
        self.asr: Any = None
        self.tts: Any = None
        self.loaded_at: float | None = None
        self.load_error: str | None = None

    def load(self) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("cuda_not_available")
        if not ASR_MODEL_PATH.is_file():
            raise RuntimeError("nemotron_asr_model_missing")
        if not TTS_MODEL_PATH.is_dir():
            raise RuntimeError("qwen3_tts_model_missing")
        from nemo.collections.asr.models import ASRModel
        from qwen_tts import Qwen3TTSModel

        self.asr = ASRModel.restore_from(str(ASR_MODEL_PATH), map_location="cuda:0")
        self.asr.eval()
        kwargs: dict[str, Any] = {"device_map": "cuda:0", "dtype": torch.bfloat16}
        signature = inspect.signature(Qwen3TTSModel.from_pretrained)
        if "attn_implementation" in signature.parameters:
            kwargs["attn_implementation"] = ATTN_IMPLEMENTATION
        self.tts = Qwen3TTSModel.from_pretrained(str(TTS_MODEL_PATH), **kwargs)
        self.loaded_at = time.time()
        self.load_error = None

    def transcribe(self, input_path: Path) -> tuple[str, str]:
        if self.asr is None:
            raise RuntimeError("asr_not_ready")
        output = self.asr.transcribe([str(input_path)], batch_size=1)
        first = output[0] if output else ""
        text = str(getattr(first, "text", first) or "").strip()
        tagged = re.search(r"\s*<([a-z]{2})(?:-[A-Z]{2})?>\s*$", text)
        language = tagged.group(1).lower() if tagged else ""
        if tagged:
            text = text[: tagged.start()].strip()
        return text, language

    def synthesize(self, *, text: str, language: str) -> tuple[int, np.ndarray]:
        if self.tts is None:
            raise RuntimeError("tts_not_ready")
        language_name = SUPPORTED_LANGUAGES[language]
        kwargs: dict[str, Any] = {"text": text, "language": language_name, "speaker": TTS_SPEAKER}
        output = self.tts.generate_custom_voice(**kwargs)
        if not isinstance(output, tuple) or len(output) != 2:
            raise RuntimeError("qwen3_tts_invalid_result")
        wavs, sample_rate = output
        waveform = np.asarray(wavs[0] if isinstance(wavs, (list, tuple)) else wavs, dtype=np.float32).reshape(-1)
        if waveform.size == 0:
            raise RuntimeError("qwen3_tts_empty_audio")
        return int(sample_rate), waveform


runtime = VoiceModels()


def _write_wav(path: Path, sample_rate: int, waveform: np.ndarray) -> None:
    pcm = np.clip(waveform, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())


@asynccontextmanager
async def lifespan(_: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(runtime.load)
    except Exception as exc:
        runtime.load_error = type(exc).__name__
        raise
    yield


app = FastAPI(title="Nexus Voice Models", version="2026-07-19", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    ready = runtime.asr is not None and runtime.tts is not None and runtime.load_error is None
    if not ready:
        raise HTTPException(status_code=503, detail="voice_models_not_ready")
    return {
        "status": "ok",
        "service": "nexus-voice-models",
        "asr": "Nemotron-3.5-ASR-Streaming-0.6B",
        "tts": "Qwen3-TTS-1.7B-CustomVoice",
        "attention_backend": ATTN_IMPLEMENTATION,
        "languages": sorted(SUPPORTED_LANGUAGES),
    }


@app.post("/stt")
async def stt(file: UploadFile = File(...)) -> dict[str, object]:
    suffix = Path(file.filename or "audio.wav").suffix.lower() or ".wav"
    if suffix != ".wav":
        raise HTTPException(status_code=415, detail="wav_required")
    descriptor, raw_path = tempfile.mkstemp(prefix="stt-", suffix=".wav", dir=OUTPUT_DIR)
    os.close(descriptor)
    input_path = Path(raw_path)
    try:
        payload = await file.read()
        if not payload or len(payload) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="invalid_audio_size")
        input_path.write_bytes(payload)
        started = time.monotonic()
        text, language = await asyncio.to_thread(runtime.transcribe, input_path)
        return {"text": text, "language": language, "elapsed_ms": int((time.monotonic() - started) * 1000)}
    finally:
        input_path.unlink(missing_ok=True)


@app.post("/tts")
async def tts(payload: TtsRequest) -> dict[str, object]:
    language = payload.language.strip().lower().split("-", 1)[0]
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=422, detail="tts_language_not_supported")
    started = time.monotonic()
    sample_rate, waveform = await asyncio.to_thread(runtime.synthesize, text=payload.text.strip(), language=language)
    descriptor, raw_path = tempfile.mkstemp(prefix="tts-", suffix=".wav", dir=OUTPUT_DIR)
    os.close(descriptor)
    output_path = Path(raw_path)
    _write_wav(output_path, sample_rate, waveform)
    return {
        "path": str(output_path),
        "sample_rate": sample_rate,
        "engine": "qwen3-tts-customvoice",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
