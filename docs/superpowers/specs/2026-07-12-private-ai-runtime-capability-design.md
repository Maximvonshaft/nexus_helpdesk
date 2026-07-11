# Private AI Runtime Capability Contract Design

Date: 2026-07-12  
Work Item: #586  
Stack parent: #595  
Contract: `nexus.ai_runtime.capabilities.v1`

## 1. Objective

Nexus must not infer Private AI Runtime readiness from a configured URL, a token file, or a successful generation response. Before a Runtime can serve an authoritative Provider request, Nexus must verify one authenticated, machine-readable capability manifest against the exact approved candidate expectations.

The contract separates three independent capability families:

1. **Generation** — model identity, endpoint shape, request/response contract versions and structured-output support.
2. **Retrieval** — backend, embedding model and dimension, reranker identity and active collection alias.
3. **Voice** — STT, TTS and live-voice availability and model identities.

A second generation-model field is not a substitute for retrieval capability. RAG is represented as generation plus retrieval, not as `rag_model`.

## 2. Delivery boundary

#586 owns:

- the strict versioned manifest schema;
- the authenticated read-only Runtime endpoint implementation;
- the bounded Nexus HTTP client and compatibility evaluator;
- the Provider gate that blocks generation when the Runtime contract is missing or incompatible;
- bounded Admin/Debug evidence;
- candidate configuration, smoke checks, CI fixtures and operational documentation.

#591 continues to own deployment-as-code, host identity, systemd/Compose, model installation, Qdrant backup/restore, clean-host rebuild and rollback packages. This design adds `infra/private_ai_runtime/capability_api.py` as service-level endpoint code only; it does not deploy or activate it.

#595 owns Provider traffic selection. #586 is stacked on #595 and must preserve control, shadow, canary and kill-switch semantics. The capability gate runs only when the selected path would call the candidate Runtime. A failed gate never falls through to customer-visible output.

## 3. Manifest schema

The endpoint returns one JSON object no larger than 32 KiB:

```json
{
  "schema": "nexus.ai_runtime.capabilities.v1",
  "runtime": {
    "id": "nexus-private-ai-runtime",
    "version": "2026.07.12.1"
  },
  "readiness": {
    "state": "ready",
    "reason_codes": []
  },
  "generation": {
    "model": "nexus-gemma4-e4b:latest",
    "structured_output": true,
    "api_path": "/api/chat",
    "request_contract": "ollama.chat.v1",
    "response_contract": "nexus_webchat_runtime_reply_v1"
  },
  "retrieval": {
    "enabled": true,
    "backend": "qdrant",
    "embedding_model": "qwen3-embedding",
    "embedding_dimension": 1024,
    "reranker_enabled": true,
    "reranker_model": "qwen3-reranker",
    "collection_alias": "nexus-knowledge-active"
  },
  "voice": {
    "stt": {"enabled": true, "model": "faster-whisper-large-v3"},
    "tts": {"enabled": true, "model": "kokoro"},
    "live_voice": true
  }
}
```

The JSON example illustrates the shape only. Candidate values are supplied by reviewed configuration; Nexus does not guess an embedding dimension, Runtime version or active alias. Candidate and production templates intentionally leave values that are not established by repository evidence unset, which keeps readiness fail-closed until an operator binds the approved manifest.

### 3.1 Structural rules

- Unknown fields are rejected at every object level.
- Duplicate JSON keys are rejected by the client and endpoint loader.
- The schema value must equal `nexus.ai_runtime.capabilities.v1` exactly.
- Identifier values are 1–128 characters and use a bounded identifier alphabet.
- API paths are relative absolute paths beginning with `/`; schemes, authorities, queries and fragments are prohibited.
- `embedding_dimension` is an integer from 1 through 65,536; booleans and numeric strings are rejected.
- `reason_codes` contains at most 16 unique values from the bounded Runtime reason-code vocabulary.
- A `ready` manifest has no reason codes and all required generation/retrieval fields.
- A `not_ready` manifest has at least one bounded reason code.
- Disabled STT or TTS uses `model: null`; enabled capability requires a model identifier.
- Secret-like keys such as token, authorization, password, key, URL and credential are rejected anywhere in the manifest.

## 4. Approved expectations

Nexus loads the exact expected identity from server-side environment configuration:

- `PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID`
- `PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION`
- `PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL`
- `PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH`
- `PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT`
- `PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT`
- `PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND`
- `PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL`
- `PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION`
- `PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL`
- `PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS`
- `PRIVATE_AI_RUNTIME_CAPABILITIES_PATH`, default `/v1/capabilities`

Missing or malformed expectations produce `capability_expectation_missing` or `capability_expectation_invalid`; the Provider call is suppressed.

The active registered adapter uses `PRIVATE_AI_RUNTIME_GENERATION_MODEL`, defaulting to the verified repository finding `nexus-gemma4-e4b:latest`. Legacy `PRIVATE_AI_RUNTIME_DIRECT_MODEL` and `PRIVATE_AI_RUNTIME_RAG_MODEL` are accepted only when both, if present, exactly equal the generation model. Conflicting legacy values fail closed with `private_ai_runtime_legacy_model_configuration_invalid`.

## 5. Authenticated Runtime endpoint

`infra/private_ai_runtime/capability_api.py` provides a FastAPI router for `GET /v1/capabilities`.

- Authentication uses `Authorization: Bearer <token>` and constant-time comparison with a root-managed token file.
- The route is read-only and performs no model load, generation, retrieval, voice action, database write or outbound request.
- The manifest is loaded from a root-managed JSON file, parsed with duplicate-key rejection and validated before serving.
- Authentication failure returns a generic `401`; manifest failure returns a generic `503` with a bounded reason code.
- Neither response includes file paths, token values, internal addresses, stack traces or raw exception text.

## 6. Nexus probe and Provider gate

`runtime_capabilities.py` owns:

- strict manifest parsing and validation;
- exact expectation comparison;
- a no-redirect, relative-path-only HTTP GET client;
- response status/content-type/size bounds;
- token-file loading with no token in result or exception text;
- a bounded `CapabilityProbeResult` containing only status, reason codes and safe capability identity;
- a small in-process TTL cache keyed by the expected contract digest.

`CapabilityVerifiedPrivateAIRuntimeAdapter` subclasses the existing adapter. Its `generate()` flow is:

1. run the inherited static configuration checks;
2. verify the capability manifest through the cached probe;
3. if not ready, return `ProviderResult.unavailable` with `structured_output=None`, `fallback_allowed=False` and bounded `runtime_capability` evidence;
4. if ready, call the existing generation implementation;
5. attach only the safe capability evidence to the Provider safe summary.

The Provider Registry registers this verified adapter. Existing tests may instantiate the base adapter directly for lower-level output-contract behavior, while all production routing resolves the verified adapter.

## 7. Failure contract

Public/runtime reason codes are low-cardinality. Nexus normalizes transport and compatibility failures to:

- `capability_disabled`
- `capability_expectation_missing`
- `capability_expectation_invalid`
- `capability_token_missing`
- `capability_endpoint_invalid`
- `capability_timeout`
- `capability_unreachable`
- `capability_http_error`
- `capability_content_type_invalid`
- `capability_payload_too_large`
- `capability_payload_malformed`
- `capability_schema_unsupported`
- `capability_runtime_identity_mismatch`
- `capability_runtime_version_mismatch`
- `capability_generation_model_mismatch`
- `capability_generation_contract_mismatch`
- `capability_retrieval_backend_mismatch`
- `capability_embedding_model_mismatch`
- `capability_embedding_dimension_mismatch`
- `capability_reranker_missing`
- `capability_reranker_model_mismatch`
- `capability_collection_alias_mismatch`
- `capability_runtime_not_ready`

No raw exception, URL, host, token, manifest body or customer content is included in the Provider result, Admin response, log or CI artifact.

## 8. Admin and release evidence

Admin surfaces expose:

- expected schema and bounded expected identifiers;
- last probe status and reason codes;
- manifest runtime ID/version;
- generation/retrieval/voice capability booleans and bounded identifiers;
- `secret_values_exposed=false` and `internal_endpoint_exposed=false`.

A dedicated privileged read-only probe endpoint may refresh the probe. The ordinary status endpoint remains non-mutating and may report cached evidence without performing an implicit external call.

Candidate smoke and CI evidence record only the bounded `CapabilityProbeResult`. Exact-candidate release evidence can hash this safe result together with source/config identity; no Runtime URL or bearer token enters artifacts.

## 9. Testing strategy

Tests follow RED–GREEN–REFACTOR and cover:

1. strict valid manifest parsing;
2. duplicate/unknown/secret-like fields;
3. missing, malformed and oversized payloads;
4. stale schema and downgraded Runtime version;
5. generation model, endpoint and contract mismatch;
6. embedding dimension/model, reranker and alias mismatch;
7. independent generation, retrieval and voice capability representation;
8. endpoint authentication and bounded failure responses;
9. no-redirect/no-token-leak client behavior;
10. verified adapter suppressing generation on failed capability checks;
11. successful gate preserving #595 traffic and existing output-contract behavior;
12. Admin output redaction;
13. active configuration and documentation containing no stale Qwen generation defaults.

## 10. Rollback

Revert the #586 commit(s) and restore the prior Provider Registry factory and configuration templates. No migration, data repair, queue replay, external cleanup or Runtime state mutation is required. Rollback removes the capability gate and therefore reintroduces `NEX-AIR-003`; it is not an acceptable production-release state.
