# Private AI Runtime Capability Contract

Contract schema: `nexus.ai_runtime.capabilities.v1`  
Probe evidence schema: `nexus.ai_runtime.capability_probe.v1`  
Expectation evidence schema: `nexus.ai_runtime.capability_expectation.v1`

## Purpose

Nexus OSR must verify the exact Private AI Runtime identity and supported capabilities before an authoritative generation call. URL reachability, token presence and a successful model response are insufficient release evidence.

The contract separates:

- **generation**: model, structured-output support, API path and request/response contracts;
- **retrieval**: backend, embedding model and dimension, reranker and active collection alias;
- **voice**: STT, TTS and live-voice capability.

Retrieval is not represented as a second generation model.

## Runtime manifest

```json
{
  "schema": "nexus.ai_runtime.capabilities.v1",
  "runtime": {
    "id": "nexus-private-ai-runtime",
    "version": "<runtime-version>"
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
    "embedding_model": "<embedding-model>",
    "embedding_dimension": 1024,
    "reranker_enabled": true,
    "reranker_model": "<reranker-model>",
    "collection_alias": "<active-alias>"
  },
  "voice": {
    "stt": {"enabled": false, "model": null},
    "tts": {"enabled": false, "model": null},
    "live_voice": false
  }
}
```

The numeric dimension above illustrates the JSON type and is not production authority. Release configuration must use the dimension proven by the approved Runtime.

## Structural constraints

- UTF-8 JSON object, maximum 32 KiB.
- Unknown keys and duplicate keys are rejected at every object level.
- Keys containing token, authorization, password, credential, secret, API-key or endpoint-authority semantics are rejected.
- Identifiers are bounded to 128 characters and a restricted identifier alphabet.
- API paths are relative absolute paths beginning with `/`; schemes, authorities, queries and fragments are rejected.
- Embedding dimension is a non-boolean integer from 1 through 65,536.
- `ready` requires no Runtime reason codes; `not_ready` requires one or more bounded Runtime reason codes.
- Enabled STT/TTS requires a model; disabled STT/TTS requires `model: null`.
- Retrieval enabled requires backend, embedding model/dimension, collection alias and explicit reranker state.

## Authentication and transport

The Runtime exposes authenticated `GET /v1/capabilities` using a root-managed bearer-token file. The endpoint is read-only, returns `Cache-Control: no-store`, validates the manifest before serving and emits only bounded generic failures.

The Nexus client:

- reads the token from a server-side file;
- binds the path to the configured Runtime origin;
- rejects redirects;
- requires HTTP 200 and `application/json`;
- enforces the 32 KiB payload bound;
- returns only a safe probe summary.

No token, Runtime authority, file path, raw manifest, upstream payload, customer text, exception message or stack trace enters Admin responses, audit summaries or CI artifacts.

## Exact expectations

Nexus requires the following server-side values:

```text
PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_ID
PRIVATE_AI_RUNTIME_EXPECTED_RUNTIME_VERSION
PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_MODEL
PRIVATE_AI_RUNTIME_EXPECTED_GENERATION_PATH
PRIVATE_AI_RUNTIME_EXPECTED_REQUEST_CONTRACT
PRIVATE_AI_RUNTIME_EXPECTED_RESPONSE_CONTRACT
PRIVATE_AI_RUNTIME_EXPECTED_RETRIEVAL_BACKEND
PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_MODEL
PRIVATE_AI_RUNTIME_EXPECTED_EMBEDDING_DIMENSION
PRIVATE_AI_RUNTIME_EXPECTED_RERANKER_MODEL
PRIVATE_AI_RUNTIME_EXPECTED_COLLECTION_ALIAS
```

Missing, malformed or mismatched expectations fail closed. Operators must not change expectations merely to match an unexpected Runtime response; the candidate and approved evidence must be reconciled.

## Nexus execution order

1. Provider traffic authority resolves kill switch, control, shadow or canary selection.
2. A non-candidate path returns without contacting the Runtime.
3. The active adapter validates static configuration and legacy model migration state.
4. Nexus performs or reuses a bounded capability probe.
5. Any not-ready result returns `structured_output=null` and `fallback_allowed=false`; the generation request is not sent.
6. Only an exact ready match reaches the existing Private AI Runtime generation adapter.
7. The Provider result includes only bounded `runtime_capability` evidence.
8. Existing governed customer-visible message and tool-policy boundaries remain authoritative.

## Compatibility and migration

The active model variable is:

```text
PRIVATE_AI_RUNTIME_GENERATION_MODEL=nexus-gemma4-e4b:latest
```

Legacy `PRIVATE_AI_RUNTIME_DIRECT_MODEL` and `PRIVATE_AI_RUNTIME_RAG_MODEL` are temporary migration inputs only. When present, both must equal the active generation model. A conflicting value returns `private_ai_runtime_legacy_model_configuration_invalid` and suppresses generation.

## Bounded Nexus reason codes

Configuration/transport:

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

Compatibility:

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

Runtime endpoint:

- `capability_unauthorized`
- `capability_token_unavailable`
- `capability_manifest_unavailable`

Reason codes are deliberately low-cardinality. They do not include identifiers, values, addresses or exception details.

## Admin surfaces

- `GET /api/admin/provider-runtime/status`: static, non-network status and expected identity.
- `GET /api/admin/provider-runtime/capabilities/probe`: privileged, explicit, read-only live probe.

Neither surface may expose the Runtime origin or bearer token.

## Verification matrix

The Provider Runtime gate covers:

- valid exact manifest;
- unsupported schema and stale Runtime version;
- malformed, duplicate, unknown, secret-like and oversized payloads;
- generation model/path/request/response mismatch;
- retrieval backend, embedding model/dimension, reranker and alias mismatch;
- independent voice availability;
- endpoint authorization and bounded failures;
- no-redirect and no-secret-leak client behavior;
- zero generation calls on failed capability verification;
- active Registry resolving only the verified adapter;
- Admin/static status redaction;
- active templates, Smoke and runbook containing no stale Qwen generation authority.

## Rollback

Reverting this capability layer requires no migration, data repair, queue replay or external cleanup. It restores unverified Runtime behavior and therefore reopens `NEX-AIR-003`; it is not a production-ready state.
