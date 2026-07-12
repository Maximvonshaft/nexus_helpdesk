# AI Resource Server Capability Probe

## Purpose

`scripts/ai/probe_ai_resource_server.py` inventories operator-declared AI infrastructure and maps the observed contracts to Nexus OSR.

It covers:

- LLM model inventory and bounded chat/Responses compatibility;
- OpenAI-compatible and Ollama embeddings, including returned vector dimension;
- RAG query health and declared ingestion path without invoking ingestion;
- common read-only Qdrant, Weaviate and Chroma metadata surfaces;
- Nexus WebCall STT, LLM and TTS bridge contracts;
- OpenAI-compatible STT/TTS transport availability;
- declared WebSocket/LiveKit-style voice endpoint handshakes;
- Nexus configuration recommendations for Provider Runtime, Knowledge Runtime v2 and WebCall AI.

The probe is an inventory tool. It is not a production readiness decision and does not authorize Provider traffic or deployment.

## Safety boundary

The following rules are enforced by code:

- only URLs explicitly declared by the operator are contacted;
- no port range scanning, DNS crawling, link following or endpoint recursion;
- TLS verification is enabled by default;
- redirects to another origin are rejected;
- RAG upsert, upload, ingest, delete, collection mutation, tool, action and outbound paths are never called;
- passive mode performs only bounded `GET` and `OPTIONS` requests;
- active mode uses fixed synthetic input and a bounded number of inference calls;
- active STT is skipped unless an explicit synthetic WAV is supplied;
- tokens, Authorization headers, prompts, transcripts, provider response bodies and generated audio are never written to the report;
- response evidence contains only status, latency, byte count, digest and bounded structural metadata;
- output permissions are set to `0600` where supported.

Use separate target entries when LLM, RAG, vector, STT or TTS services are on different origins. Cross-origin endpoint aliases inside one target are rejected. Do not place credentials in HTTP or WebSocket URLs; use environment-variable or read-only-file secret sources.

## Passive inventory

Copy the example config outside the repository or into an ignored local path and replace only URLs, model hints and **secret source names**. Do not paste secret values into the file.

```bash
cp config/ai-resource-probe.example.json /tmp/ai-resource-probe.json
export AI_RUNTIME_PROBE_TOKEN='...'
export VECTOR_PROBE_API_KEY='...'

python scripts/ai/probe_ai_resource_server.py \
  --config /tmp/ai-resource-probe.json \
  --output artifacts/ai-resource-probe.json \
  --pretty
```

A single target can be checked without a config file:

```bash
python scripts/ai/probe_ai_resource_server.py \
  --base-url http://127.0.0.1:11434 \
  --profiles common,ollama \
  --output artifacts/ollama-capabilities.json \
  --pretty
```

Passive results can identify exposed models, endpoint families, auth requirements, health surfaces and vector-store type hints. They cannot prove generation quality or payload compatibility.

## Active compatibility probe

Active mode produces small compute calls. Enable only the required tests.

Example target fragment:

```json
{
  "name": "private-ai-runtime",
  "base_url": "https://ai-runtime.example.internal",
  "profiles": ["openai", "ollama", "nexus_runtime"],
  "mode": "active",
  "active_tests": [
    "ollama_chat",
    "openai_embeddings",
    "nexus_llm_bridge",
    "nexus_rag_question"
  ],
  "max_active_calls": 8,
  "models": {
    "llm": "qwen3:8b",
    "embedding": "bge-m3",
    "embedding_dimension": "1024"
  },
  "endpoints": {
    "nexus_llm": "/v1/respond",
    "nexus_rag": "/chat/rag",
    "rag_upsert_declared": "/rag/upsert"
  }
}
```

Supported active test names:

- `openai_chat`
- `openai_responses`
- `openai_embeddings`
- `ollama_chat`
- `ollama_embeddings`
- `nexus_llm_bridge`
- `nexus_rag_question`
- `openai_stt`
- `nexus_stt_bridge`
- `openai_tts`
- `nexus_tts_bridge`

`active_tests: "auto"` selects bounded tests based on discovered endpoints. Explicit test lists are preferred for production-adjacent infrastructure.

### STT sample requirement

STT testing needs a synthetic WAV containing no customer or operational data:

```json
{
  "mode": "active",
  "active_tests": ["nexus_stt_bridge"],
  "stt_sample_file": "/secure/local/path/nexus-probe.wav"
}
```

The WAV is uploaded only to the declared STT endpoint. The report retains its SHA-256, sample metadata and returned transcript length/hash, not the audio or transcript.

## Report contract

The output schema is `nexus.ai_resource_probe.v1`.

Each target contains:

- safe base URL and selected profiles;
- TLS/auth-source readiness without credential values;
- bounded endpoint evidence;
- model inventory grouped into LLM, embedding, STT, TTS, reranker and unknown;
- active test status and structural response evidence;
- WebSocket handshake status when declared;
- `capabilities` inventory;
- `nexus_compatibility` with suggested environment keys;
- explicit side-effect counters.

The report never edits `.env` or Nexus configuration. Recommendations must be reviewed before use.

## Nexus mapping rules

### Provider Runtime

Directly compatible contracts currently include:

- Ollama `/api/chat` with `PRIVATE_AI_RUNTIME_REQUEST_SHAPE=ollama_chat`;
- Nexus-style `system` + `input` bridge with `request_shape=system_input`;
- Nexus `/chat/direct` or `/chat/rag` question bridge with `request_shape=question`.

A standard OpenAI `/v1/chat/completions` server is marked `adapter_review_required` because the current Nexus `messages` shape emits `response_format` as a string. A standard OpenAI `/v1/responses` service is marked `adapter_required`; the current private runtime adapter does not emit the canonical Responses request.

### Knowledge Runtime v2

A successful OpenAI-compatible embedding probe maps to:

- `KNOWLEDGE_EMBEDDING_PROVIDER=openai_compatible`
- `KNOWLEDGE_EMBEDDING_BASE_URL=<base ending in /v1>`
- `KNOWLEDGE_EMBEDDING_MODEL=<observed model>`
- `KNOWLEDGE_EMBEDDING_DIM=<observed dimension>`
- `KNOWLEDGE_EMBEDDING_DIMENSION_REQUEST_SUPPORTED=<verified boolean>`

Nexus currently requires the configured embedding dimension to equal its knowledge vector contract. A successful provider call with another dimension does not make it compatible until the Nexus schema/contract is deliberately changed.

The probe may record `/rag/upsert` as declared, but it never calls it. Use the existing `backend/scripts/sync_ai_runtime_rag.py --dry-run` path before separately authorized ingestion.

### WebCall AI

The direct Nexus voice bridge contracts are:

- STT multipart field `audio`, plus `language`, `sample_rate`, `channels`; JSON response `text`, `language`, `confidence`;
- LLM JSON body `system`, `input`, `language`, `response_format`; response `response_text`, `intent`, `handoff_required`, `handoff_reason`;
- TTS JSON body `text`, `language`, `voice`, `format=wav`; response WAV or PCM.

OpenAI STT uses multipart field `file`, so it is reported as requiring a bridge for the current WebCall contract. TTS compatibility must be confirmed against the configured bridge request and audio response types.

## What to provide for a real server review

Provide either:

1. the generated `nexus.ai_resource_probe.v1` JSON report; or
2. service base URLs, endpoint paths, model hints and the **names/paths of secret sources** available on the server.

Do not send token values in chat. Running the script on the server and sharing the redacted report is the preferred path.
