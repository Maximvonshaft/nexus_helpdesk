from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path
from typing import Mapping

MODULE_PATH = Path(__file__).resolve().parents[1] / "probe_ai_resource_server.py"
SPEC = importlib.util.spec_from_file_location("probe_ai_resource_server", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeTransport:
    def __init__(self, responses: Mapping[tuple[str, str], tuple[int, object, str]] | None = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[dict[str, object]] = []

    def request(self, *, target, method: str, url: str, headers=None, body=None):
        path = MODULE.urllib.parse.urlsplit(url).path
        self.calls.append({"method": method, "path": path, "headers": dict(headers or {}), "body": body})
        status, payload, content_type = self.responses.get((method, path), (404, {"error": "not found"}, "application/json"))
        if isinstance(payload, bytes):
            raw = payload
        else:
            raw = json.dumps(payload).encode("utf-8")
        return MODULE.HttpResponse(
            url=url,
            method=method,
            status=status,
            headers={"Content-Type": content_type},
            body=raw,
            latency_ms=7,
            error_code=None if status < 400 else f"http_{status}",
        )


class ProbeConfigTests(unittest.TestCase):
    def test_rejects_cross_origin_declared_endpoint(self) -> None:
        payload = {
            "schema": MODULE.CONFIG_SCHEMA,
            "targets": [
                {
                    "name": "runtime",
                    "base_url": "https://ai.example.test",
                    "endpoints": {"nexus_llm": "https://other.example.test/v1/respond"},
                }
            ],
        }
        with self.assertRaisesRegex(MODULE.ProbeConfigError, "cross_origin"):
            MODULE.parse_config(payload)

    def test_rejects_write_endpoint_except_declared_rag_upsert(self) -> None:
        payload = {
            "schema": MODULE.CONFIG_SCHEMA,
            "targets": [
                {
                    "name": "runtime",
                    "base_url": "https://ai.example.test",
                    "endpoints": {"danger": "/rag/upsert"},
                }
            ],
        }
        with self.assertRaisesRegex(MODULE.ProbeConfigError, "write_endpoint_forbidden"):
            MODULE.parse_config(payload)

        payload["targets"][0]["endpoints"] = {"rag_upsert_declared": "/rag/upsert"}
        config = MODULE.parse_config(payload)
        self.assertEqual(config.targets[0].endpoints["rag_upsert_declared"], "https://ai.example.test/rag/upsert")

    def test_rejects_url_credentials(self) -> None:
        payload = {
            "schema": MODULE.CONFIG_SCHEMA,
            "targets": [{"name": "runtime", "base_url": "https://user:secret@ai.example.test"}],
        }
        with self.assertRaises(MODULE.ProbeConfigError):
            MODULE.parse_config(payload)


class ProbeBehaviorTests(unittest.TestCase):
    def test_passive_probe_never_calls_declared_write_endpoint(self) -> None:
        target = MODULE.TargetConfig(
            name="runtime",
            base_url="https://ai.example.test",
            profiles=("nexus_runtime",),
            endpoints={"rag_upsert_declared": "https://ai.example.test/rag/upsert"},
        )
        transport = FakeTransport()
        report = MODULE.probe_target(target, transport=transport)
        paths = [call["path"] for call in transport.calls]
        self.assertNotIn("/rag/upsert", paths)
        self.assertEqual(report["side_effects"]["write_calls"], 0)
        self.assertEqual(report["declared_write_endpoints_not_called"][0]["path"], "/rag/upsert")

    def test_openai_models_inventory_and_embedding_dimension_are_safe(self) -> None:
        target = MODULE.TargetConfig(
            name="runtime",
            base_url="https://ai.example.test",
            profiles=("openai",),
            mode="active",
            active_tests=("openai_embeddings",),
            models={"embedding": "bge-m3", "embedding_dimension": "1024"},
        )
        transport = FakeTransport(
            {
                ("GET", "/v1/models"): (
                    200,
                    {"data": [{"id": "qwen3:8b"}, {"id": "bge-m3"}, {"id": "whisper-large-v3"}, {"id": "kokoro-tts"}]},
                    "application/json",
                ),
                ("POST", "/v1/embeddings"): (
                    200,
                    {"data": [{"index": 0, "embedding": [0.0] * 1024}], "model": "bge-m3"},
                    "application/json",
                ),
            }
        )
        report = MODULE.probe_target(target, transport=transport)
        self.assertIn("qwen3:8b", report["model_categories"]["llm"])
        self.assertIn("bge-m3", report["model_categories"]["embedding"])
        active = report["active_tests"][0]
        self.assertEqual(active["safe_response"]["embedding_dimension"], 1024)
        self.assertNotIn("embedding", json.dumps(active["safe_response"]).lower().replace("embedding_dimension", ""))
        mapping = report["nexus_compatibility"]["knowledge_embeddings"]
        self.assertEqual(mapping["fit"], "direct")
        self.assertEqual(mapping["environment"]["KNOWLEDGE_EMBEDDING_DIM"], 1024)

    def test_ollama_maps_directly_to_current_nexus_adapter(self) -> None:
        target = MODULE.TargetConfig(
            name="ollama",
            base_url="http://127.0.0.1:11434",
            profiles=("ollama",),
            mode="active",
            active_tests=("ollama_chat",),
            models={"llm": "qwen3:8b"},
        )
        transport = FakeTransport(
            {
                ("GET", "/api/tags"): (200, {"models": [{"name": "qwen3:8b"}]}, "application/json"),
                ("POST", "/api/chat"): (200, {"model": "qwen3:8b", "message": {"role": "assistant", "content": "discarded"}}, "application/json"),
            }
        )
        report = MODULE.probe_target(target, transport=transport)
        mapping = report["nexus_compatibility"]["provider_runtime"]
        self.assertEqual(mapping["fit"], "direct")
        self.assertEqual(mapping["environment"]["PRIVATE_AI_RUNTIME_REQUEST_SHAPE"], "ollama_chat")
        self.assertEqual(mapping["environment"]["PRIVATE_AI_RUNTIME_DIRECT_PATH"], "/api/chat")
        rendered = json.dumps(report)
        self.assertNotIn("discarded", rendered)
        self.assertNotIn(MODULE.FIXED_LLM_PROMPT, rendered)

    def test_tts_response_is_hashed_not_persisted(self) -> None:
        target = MODULE.TargetConfig(
            name="voice",
            base_url="https://voice.example.test",
            profiles=("openai",),
            mode="active",
            active_tests=("openai_tts",),
            models={"tts": "kokoro-tts", "tts_voice": "af"},
        )
        wav = b"RIFF" + b"\x00" * 40 + b"WAVE" + b"audio-bytes"
        transport = FakeTransport({("POST", "/v1/audio/speech"): (200, wav, "audio/wav")})
        report = MODULE.probe_target(target, transport=transport)
        active = report["active_tests"][0]
        self.assertTrue(active["audio_detected"])
        self.assertEqual(active["response_bytes"], len(wav))
        self.assertNotIn("audio-bytes", json.dumps(report))
        self.assertFalse(report["side_effects"]["audio_persisted"])

    def test_stt_active_probe_requires_explicit_sample(self) -> None:
        target = MODULE.TargetConfig(
            name="voice",
            base_url="https://voice.example.test",
            profiles=("openai",),
            mode="active",
            active_tests=("openai_stt",),
            models={"stt": "whisper-large-v3"},
        )
        transport = FakeTransport()
        report = MODULE.probe_target(target, transport=transport)
        self.assertEqual(report["active_tests"][0]["status"], "skipped")
        self.assertEqual(report["active_tests"][0]["reason"], "stt_sample_file_missing")
        self.assertFalse(any(call["method"] == "POST" for call in transport.calls))

    def test_nexus_voice_bridge_mapping_requires_successful_contract_probes(self) -> None:
        target = MODULE.TargetConfig(
            name="voice-bridge",
            base_url="https://voice.example.test",
            profiles=("common",),
            mode="active",
            active_tests=("nexus_llm_bridge", "nexus_tts_bridge"),
            endpoints={
                "nexus_llm": "https://voice.example.test/v1/respond",
                "nexus_tts": "https://voice.example.test/v1/speech",
                "voice_health": "https://voice.example.test/health",
            },
            models={"llm": "qwen3:8b", "tts_voice": "default"},
        )
        transport = FakeTransport(
            {
                ("POST", "/v1/respond"): (200, {"response_text": "discard", "intent": "greeting", "handoff_required": False}, "application/json"),
                ("POST", "/v1/speech"): (200, b"RIFF" + b"\0" * 100, "audio/wav"),
                ("GET", "/health"): (200, {"status": "ok"}, "application/json"),
            }
        )
        report = MODULE.probe_target(target, transport=transport)
        voice = report["nexus_compatibility"]["voice"]
        self.assertIn("llm_direct", voice["fit"])
        self.assertIn("tts_direct", voice["fit"])
        self.assertEqual(voice["environment"]["LLM_ENDPOINT"], "https://voice.example.test/v1/respond")
        self.assertEqual(voice["environment"]["TTS_ENDPOINT"], "https://voice.example.test/v1/speech")
        self.assertNotIn("discard", json.dumps(report))

    def test_active_probe_error_is_isolated_to_one_test(self) -> None:
        target = MODULE.TargetConfig(
            name="voice",
            base_url="https://voice.example.test",
            profiles=("openai",),
            mode="active",
            active_tests=("openai_stt", "openai_tts"),
            models={"stt": "whisper-large-v3", "tts": "kokoro-tts"},
            stt_sample_file="/definitely/missing/probe.wav",
        )
        transport = FakeTransport({("POST", "/v1/audio/speech"): (200, b"RIFF" + b"\0" * 100, "audio/wav")})
        report = MODULE.probe_target(target, transport=transport)
        by_name = {item["test"]: item for item in report["active_tests"]}
        self.assertEqual(by_name["openai_stt"]["status"], "probe_error")
        self.assertEqual(by_name["openai_tts"]["status"], "available")

    def test_capability_inventory_separates_llm_embedding_and_voice(self) -> None:
        target = MODULE.TargetConfig(
            name="runtime",
            base_url="https://ai.example.test",
            profiles=("openai",),
        )
        transport = FakeTransport({
            ("GET", "/v1/models"): (200, {"data": [{"id": "qwen3:8b"}, {"id": "bge-m3"}, {"id": "whisper-large-v3"}]}, "application/json"),
            ("OPTIONS", "/v1/chat/completions"): (405, {}, "application/json"),
            ("OPTIONS", "/v1/embeddings"): (405, {}, "application/json"),
            ("OPTIONS", "/v1/audio/transcriptions"): (405, {}, "application/json"),
        })
        report = MODULE.probe_target(target, transport=transport)
        self.assertEqual(report["capabilities"]["llm"]["model_count"], 1)
        self.assertEqual(report["capabilities"]["embeddings"]["model_count"], 1)
        self.assertEqual(report["capabilities"]["voice"]["stt_models"], 1)
        self.assertEqual(report["capabilities"]["llm"]["openai_chat"], "method_not_allowed")

    def test_report_declares_zero_write_and_no_raw_retention(self) -> None:
        config = MODULE.ProbeConfig(
            targets=(MODULE.TargetConfig(name="runtime", base_url="https://ai.example.test", profiles=("common",)),)
        )
        report = MODULE.build_report(config, transport=FakeTransport({("GET", "/health"): (200, {"status": "ok"}, "application/json")}))
        self.assertFalse(report["safety"]["write_requests"])
        self.assertFalse(report["safety"]["raw_provider_bodies_retained"])
        self.assertFalse(report["safety"]["credentials_retained"])


if __name__ == "__main__":
    unittest.main()
