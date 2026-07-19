from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "infra" / "private-ai-runtime" / "live_voice_runtime" / "voice_policy.py"


def _policy_module():
    spec = importlib.util.spec_from_file_location("live_voice_policy_test", POLICY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asr_quality_gate_rejects_mixed_script_hallucinations() -> None:
    policy = _policy_module()

    assert policy.transcript_quality_reason("お南d you can support.", "ja") == "script_mismatch"
    assert policy.transcript_quality_reason("How many languages do you support?", "en") is None
    assert policy.transcript_quality_reason("你好，我想查询包裹状态。", "zh") is None


def test_tts_chunker_preserves_text_and_bounds_first_playable_unit() -> None:
    policy = _policy_module()
    answer = "First sentence is concise. Second sentence makes the answer longer. Third sentence completes it."

    chunks = policy.split_tts_chunks(answer, max_chars=45)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 45 for chunk in chunks)
    assert " ".join(chunks) == answer
