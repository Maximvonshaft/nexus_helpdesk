from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "infra" / "private-ai-runtime" / "live_voice_runtime" / "app.py"


def test_voice_media_edge_has_no_customer_reply_generation() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert "def fast_rule_answer" not in source
    assert "Please provide your tracking number" not in source
    assert "Could you please repeat your request?" not in source
    assert "def llm_answer_sync" not in source
    assert "def needs_rag" not in source
    assert "def rag_context_sync" not in source
    assert "OLLAMA_URL" not in source
    assert "RAG_URL" not in source
    assert "orchestrate_sync" in source
    assert "NEXUS_LIVE_VOICE_TURN_URL" in source
    assert "HTML =" not in source
    assert "HTMLResponse" not in source


def test_voice_media_edge_uses_runtime_reply_language_for_tts() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert 'response_language = str(result.get("language")' in source
    assert "tts_sync, answer, response_language" in source
    assert "kokoro-fallback" not in source
    assert 'raise RuntimeError("tts_language_not_supported")' in source


def test_voice_runtime_prevents_echo_barge_in_and_streams_complete_audio() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert "self.ignore_audio_until = 0.0" in source
    assert "if asyncio.get_running_loop().time() < self.ignore_audio_until" in source
    assert "duration_seconds + 0.35" in source
    assert "for offset in range(0, len(pcm_bytes), chunk_bytes)" in source
    assert '"duration_ms": round(duration_seconds * 1000)' in source
    assert "barge_in" not in source


def test_voice_runtime_deletes_transient_input_audio() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert "wav_path.unlink(missing_ok=True)" in source
