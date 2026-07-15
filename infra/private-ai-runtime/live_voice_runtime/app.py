import asyncio
import json
import math
import os
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


BASE = Path("/data/ai-runtime")
APP_DIR = BASE / "services" / "live_voice_call_demo"
INPUT_DIR = APP_DIR / "input"
INPUT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = BASE / "a10_ollama_provider_token.txt"

VOICE_URL = os.getenv("LIVE_VOICE_API_URL", "http://127.0.0.1:8010").rstrip("/")
GERMAN_TTS_URL = os.getenv("LIVE_VOICE_GERMAN_TTS_URL", "http://127.0.0.1:8040").rstrip("/")
RAG_URL = os.getenv("LIVE_VOICE_RAG_URL", "http://127.0.0.1:8020").rstrip("/")
OLLAMA_URL = os.getenv("LIVE_VOICE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

MODEL = os.getenv("LIVE_VOICE_MODEL", "nexus-gemma4-e4b:latest")
COLLECTION = os.getenv("LIVE_VOICE_RAG_COLLECTION", "swiss_customer_service_kb")

SAMPLE_RATE_IN = 16000

app = FastAPI(title="Nexus Live Voice Runtime", version="2026-07-15")


def read_token() -> str:
    try:
        return TOKEN_FILE.read_text().strip()
    except Exception:
        return ""


async def send_json(ws: WebSocket, lock: asyncio.Lock, obj: dict):
    async with lock:
        await ws.send_text(json.dumps(obj, ensure_ascii=False))


async def send_binary(ws: WebSocket, lock: asyncio.Lock, data: bytes):
    async with lock:
        await ws.send_bytes(data)


def write_wav_pcm16(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE_IN):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

def wav_file_to_pcm16_bytes(path: Path) -> tuple[int, bytes]:
    """
    Browser frontend expects raw little-endian PCM16 bytes, not a WAV container.
    This converts generated WAV files from Kokoro/Piper into raw PCM16.
    """
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if sample_width == 2:
        arr = np.frombuffer(frames, dtype=np.int16)
    elif sample_width == 1:
        # unsigned 8-bit PCM -> signed 16-bit
        arr8 = np.frombuffer(frames, dtype=np.uint8).astype(np.int16)
        arr = ((arr8 - 128) << 8).astype(np.int16)
    elif sample_width == 4:
        arr32 = np.frombuffer(frames, dtype=np.int32)
        arr = (arr32 / 65536).clip(-32768, 32767).astype(np.int16)
    else:
        raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)

    return sample_rate, arr.tobytes()


def rms_pcm16(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    arr = np.frombuffer(pcm, dtype=np.int16)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((arr.astype(np.float32) / 32768.0) ** 2)))


def stt_sync(wav_path: Path) -> dict:
    with open(wav_path, "rb") as f:
        files = {"file": (wav_path.name, f, "audio/wav")}
        data = {"language": "auto", "beam_size": "1"}
        r = requests.post(f"{VOICE_URL}/stt", files=files, data=data, timeout=180)
        r.raise_for_status()
        return r.json()


def selected_lang_name(lang_code: str) -> str:
    lc = (lang_code or "").lower()
    if lc in ("de", "d", "de_de"):
        return "German"
    if lc in ("f", "fr", "fr_fr"):
        return "French"
    if lc in ("i", "it", "it_it"):
        return "Italian"
    if lc in ("z", "zh", "zh_cn"):
        return "Chinese"
    return "English"


def needs_rag(text: str) -> bool:
    t = (text or "").lower()
    keys = [
        "parcel", "package", "tracking", "delivery", "lost", "address",
        "包裹", "快递", "运单", "派送", "地址", "签收",
        "paket", "sendung", "lieferung", "sendungsnummer",
    ]
    return any(k in t or k in text for k in keys)


def rag_context_sync(question: str) -> str:
    payload = {
        "collection": COLLECTION,
        "query": question,
        "limit": 3,
        "rerank_top_k": 0,
    }
    r = requests.post(f"{RAG_URL}/rag/ask-context", json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data.get("context", "") or ""


def llm_answer_sync(question: str, context: str, lang_code: str) -> str:
    lang_name = selected_lang_name(lang_code)

    system = (
        "You are a natural, capable logistics phone customer-service assistant. "
        f"Reply in {lang_name} only. "
        "Generate every customer-visible word yourself; never use canned or prewritten wording. "
        "Answer the customer's actual request completely and conversationally. "
        "Use as many short spoken sentences as needed, usually two to four. "
        "Do not invent shipment status, policy, actions, or promises that are absent from the supplied context. "
        "Return only the final spoken answer. No reasoning, labels, or markdown."
    )

    user = (
        f"Customer said: {question}\n\n"
        f"Knowledge context:\n{context}\n\n"
        f"Answer in {lang_name} only."
    )

    payload = {
        "model": MODEL,
        "think": False,
        "keep_alive": "24h",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 192,
            "num_ctx": 4096,
        },
    }

    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()
    content = ((data.get("message") or {}).get("content") or "").strip()
    content = content.replace("<think>", "").replace("</think>", "").strip()

    if not content:
        raise RuntimeError("AI Runtime returned an empty voice reply")

    return content


def tts_sync(answer: str, lang_code: str, voice: str, speed: float) -> dict:
    lc = (lang_code or "").lower()
    v = voice or ""

    if lc in ("de", "d", "de_de") or v.startswith("de_DE"):
        payload = {
            "text": answer,
            "lang_code": "de",
            "voice": "de_DE-thorsten-medium",
            "speed": speed,
        }
        r = requests.post(f"{GERMAN_TTS_URL}/tts", json=payload, timeout=300)
        r.raise_for_status()
        data = r.json()
        data["actual_engine"] = "piper-german"
        return data

    payload = {
        "text": answer,
        "lang_code": lang_code or "b",
        "voice": voice or "bm_george",
        "speed": speed,
    }

    try:
        r = requests.post(f"{VOICE_URL}/tts", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        data["actual_engine"] = "kokoro"
        return data
    except Exception:
        fallback = {
            "text": answer,
            "lang_code": "b",
            "voice": "bm_george",
            "speed": speed,
        }
        r = requests.post(f"{VOICE_URL}/tts", json=fallback, timeout=180)
        r.raise_for_status()
        data = r.json()
        data["actual_engine"] = "kokoro-fallback"
        return data


def run_turn_sync(customer_text: str, lang_code: str, voice: str, speed: float) -> dict:
    started = time.time()
    context = ""
    mode = "direct_llm"
    if needs_rag(customer_text):
        try:
            context = rag_context_sync(customer_text)
            mode = "rag_no_rerank"
        except Exception:
            context = ""

    answer = llm_answer_sync(customer_text, context, lang_code)

    speech = tts_sync(answer, lang_code, voice, speed)

    return {
        "status": "ok",
        "mode": mode,
        "customer_text": customer_text,
        "answer": answer,
        "context": context,
        "tts": speech,
        "duration_sec": round(time.time() - started, 3),
    }




@app.get("/health")
def health():
    out = {
        "status": "ok",
        "service": "live-browser-voice-call",
        "transport": "websocket-audio-stream",
        "features": ["server-vad", "streaming-audio-in", "streaming-tts-out", "echo-safe-turn-taking"],
        "model": MODEL,
        "think": False,
        "keep_alive": "24h",
        "checks": {},
    }

    for name, url in [
        ("voice", f"{VOICE_URL}/health"),
        ("german_tts", f"{GERMAN_TTS_URL}/health"),
        ("rag", f"{RAG_URL}/health"),
        ("ollama", f"{OLLAMA_URL}/api/version"),
    ]:
        try:
            r = requests.get(url, timeout=3)
            out["checks"][name] = {"ok": r.ok, "data": r.json() if r.text else None}
        except Exception as e:
            out["checks"][name] = {"ok": False, "error": repr(e)}

    return JSONResponse(out)


class LiveSession:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.send_lock = asyncio.Lock()
        self.lang_code = (ws.query_params.get("lang_code") or "de").strip()
        self.voice = (ws.query_params.get("voice") or "de_DE-thorsten-medium").strip()
        try:
            self.speed = float(ws.query_params.get("speed") or "1.0")
        except Exception:
            self.speed = 1.0

        self.turn_id = 0
        self.processing_task: Optional[asyncio.Task] = None

        self.in_speech = False
        self.frames = []
        self.speech_frames = 0
        self.silence_frames = 0

        self.rms_threshold = 0.018
        self.min_speech_frames = 8
        self.end_silence_frames = 35
        self.ignore_audio_until = 0.0

    async def handle_chunk(self, chunk: bytes):
        if asyncio.get_running_loop().time() < self.ignore_audio_until:
            self.in_speech = False
            self.frames = []
            self.speech_frames = 0
            self.silence_frames = 0
            return

        rms = rms_pcm16(chunk)
        is_speech = rms >= self.rms_threshold

        if is_speech:
            if not self.in_speech:
                self.in_speech = True
                self.frames = []
                self.speech_frames = 0
                self.silence_frames = 0
                await send_json(self.ws, self.send_lock, {
                    "type": "speech_start",
                    "rms": round(rms, 4),
                })

            self.speech_frames += 1
            self.silence_frames = 0
            self.frames.append(chunk)

        else:
            if self.in_speech:
                self.frames.append(chunk)
                self.silence_frames += 1

                if self.silence_frames >= self.end_silence_frames:
                    pcm = b"".join(self.frames)
                    duration = len(pcm) / 2 / SAMPLE_RATE_IN

                    self.in_speech = False
                    self.frames = []
                    self.silence_frames = 0

                    if self.speech_frames < self.min_speech_frames or duration < 0.55:
                        await send_json(self.ws, self.send_lock, {
                            "type": "speech_ignored",
                            "reason": "too_short",
                        })
                        return

                    if self.processing_task and not self.processing_task.done():
                        await send_json(self.ws, self.send_lock, {
                            "type": "speech_ignored",
                            "reason": "ai_busy_waiting_for_answer",
                        })
                        return

                    self.turn_id += 1
                    tid = self.turn_id
                    self.processing_task = asyncio.create_task(self.process_turn(tid, pcm))

    async def process_turn(self, tid: int, pcm: bytes):
        try:
            wav_path = INPUT_DIR / f"turn_{tid}_{int(time.time() * 1000)}.wav"
            write_wav_pcm16(wav_path, pcm, SAMPLE_RATE_IN)
            duration = len(pcm) / 2 / SAMPLE_RATE_IN

            await send_json(self.ws, self.send_lock, {
                "type": "speech_end",
                "turn_id": tid,
                "duration_sec": round(duration, 2),
            })

            await send_json(self.ws, self.send_lock, {
                "type": "stt_start",
                "turn_id": tid,
            })

            loop = asyncio.get_running_loop()
            stt = await loop.run_in_executor(None, stt_sync, wav_path)
            transcript = (stt.get("text") or "").strip()

            await send_json(self.ws, self.send_lock, {
                "type": "stt_result",
                "turn_id": tid,
                "text": transcript,
                "language": stt.get("language"),
            })

            if not transcript:
                await send_json(self.ws, self.send_lock, {
                    "type": "turn_error",
                    "turn_id": tid,
                    "message": "STT produced empty transcript",
                })
                return

            await send_json(self.ws, self.send_lock, {
                "type": "thinking_start",
                "turn_id": tid,
            })

            result = await loop.run_in_executor(
                None,
                run_turn_sync,
                transcript,
                self.lang_code,
                self.voice,
                self.speed,
            )

            await send_json(self.ws, self.send_lock, {
                "type": "ai_answer",
                "turn_id": tid,
                "customer_text": transcript,
                "answer": result["answer"],
                "mode": result.get("mode"),
                "duration_sec": result.get("duration_sec"),
            })

            tts = result.get("tts") or {}
            path = tts.get("path")
            if not path or not Path(path).exists():
                await send_json(self.ws, self.send_lock, {
                    "type": "turn_error",
                    "turn_id": tid,
                    "message": "TTS file not found",
                    "tts": tts,
                })
                return

            sample_rate, pcm_bytes = wav_file_to_pcm16_bytes(Path(path))
            duration_seconds = len(pcm_bytes) / 2 / sample_rate
            self.ignore_audio_until = asyncio.get_running_loop().time() + duration_seconds + 0.35
            chunk_bytes = max(2, int(sample_rate * 2 * 0.1))
            chunks = max(1, math.ceil(len(pcm_bytes) / chunk_bytes))

            await send_json(self.ws, self.send_lock, {
                "type": "tts_start",
                "turn_id": tid,
                "sample_rate": sample_rate,
                "answer": result["answer"],
                "engine": tts.get("actual_engine") or tts.get("engine"),
                "voice": tts.get("voice"),
                "audio_format": "pcm16le",
                "bytes": len(pcm_bytes),
                "chunks": chunks,
                "duration_ms": round(duration_seconds * 1000),
            })

            for offset in range(0, len(pcm_bytes), chunk_bytes):
                await send_binary(self.ws, self.send_lock, pcm_bytes[offset: offset + chunk_bytes])

            await send_json(self.ws, self.send_lock, {
                "type": "tts_end",
                "turn_id": tid,
            })

        except asyncio.CancelledError:
            await send_json(self.ws, self.send_lock, {
                "type": "turn_cancelled",
                "turn_id": tid,
            })
        except Exception:
            await send_json(self.ws, self.send_lock, {
                "type": "turn_error",
                "turn_id": tid,
                "message": "The voice turn could not be completed.",
            })
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.query_params.get("token", "")
    expected = read_token()

    if not expected or token != expected:
        await ws.close(code=1008)
        return

    await ws.accept()
    session = LiveSession(ws)

    await send_json(ws, session.send_lock, {
        "type": "connected",
        "message": "live voice call connected",
        "sample_rate_in": SAMPLE_RATE_IN,
        "features": ["vad", "echo-safe-turn-taking", "streaming-tts"],
        "lang_code": session.lang_code,
        "voice": session.voice,
        "speed": session.speed,
    })

    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                await session.handle_chunk(msg["bytes"])
            elif "text" in msg and msg["text"]:
                pass
    except WebSocketDisconnect:
        if session.processing_task and not session.processing_task.done():
            session.processing_task.cancel()
    except Exception:
        if session.processing_task and not session.processing_task.done():
            session.processing_task.cancel()
        try:
            await ws.close()
        except Exception:
            pass

