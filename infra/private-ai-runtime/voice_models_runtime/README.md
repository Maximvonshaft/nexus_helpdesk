# Versioned voice model service

This service is the sole model endpoint for the live-voice media edge:

- Nemotron 3.5 ASR Streaming 0.6B for automatic transcription;
- Qwen3-TTS 1.7B CustomVoice for supported-language speech synthesis.

It only binds loopback (`127.0.0.1:8010`) and is reached through the media
edge. Model paths are local, immutable release inputs configured in
`/etc/nexus/voice_models.env`; no request supplies a model name, revision or
remote URL.

The release builder creates `.venv` from a reviewed lockfile and installs any
CUDA extension before the release is activated. `app.py` has no package
installer, shell execution, model download, or source-build fallback. A host
must use the corresponding `nexus-voice-models.service` unit, including its
resource limits and dedicated writable cache/output paths.

Required non-secret environment names:

```text
NEMOTRON_ASR_MODEL_PATH=/data/ai-runtime/models/nemotron-asr.nemo
QWEN3_TTS_MODEL_PATH=/data/ai-runtime/models/Qwen3-TTS-1.7B-CustomVoice
QWEN3_TTS_SPEAKER=Ryan
VOICE_MODEL_OUTPUT_DIR=/var/lib/nexus/voice-models/output
```

`QWEN3_TTS_ATTN_IMPLEMENTATION` defaults to `sdpa`. A release may set it to a
prebuilt compatible backend only after candidate acceptance; this service
never tries to install or compile it.
