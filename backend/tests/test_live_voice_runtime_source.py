from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "infra" / "private-ai-runtime" / "live_voice_runtime" / "app.py"


def test_voice_runtime_has_no_rule_or_empty_model_customer_reply() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert "def fast_rule_answer" not in source
    assert "answer = fast_rule_answer" not in source
    assert "Please provide your tracking number" not in source
    assert "Could you please repeat your request?" not in source
    assert 'raise RuntimeError("AI Runtime returned an empty voice reply")' in source
    assert "answer = llm_answer_sync(customer_text, context, lang_code)" in source
    assert "HTML =" not in source
    assert "HTMLResponse" not in source


def test_voice_runtime_allows_complete_natural_generation() -> None:
    source = RUNTIME.read_text(encoding="utf-8")

    assert '"num_predict": 192' in source
    assert '"num_ctx": 4096' in source
    assert "usually two to four" in source
    assert "One or two short sentences." not in source


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
