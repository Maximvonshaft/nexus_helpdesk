# Private AI Runtime Capability Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every authoritative Private AI Runtime call depend on an authenticated, strict and exact capability contract while keeping all evidence bounded and secret-free.

**Architecture:** Add a strict capability schema/client module, a read-only Runtime endpoint, and a verified adapter subclass registered at the Provider boundary. Preserve #595 traffic selection by stacking on its head; the gate executes only after traffic selection chooses a candidate call. Keep deployment/rebuild work outside #586.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Provider models, urllib standard library, pytest, GitHub Actions.

## Global Constraints

- Contract schema is exactly `nexus.ai_runtime.capabilities.v1`.
- Capability endpoint is authenticated, read-only and no-redirect.
- Manifest payload limit is 32 KiB.
- Unknown fields, duplicate keys, secret-like keys and malformed identifiers fail closed.
- No Runtime URL, token, raw payload, customer text or stack trace appears in safe evidence.
- Generation, retrieval and voice remain independent capabilities.
- Retrieval is not modeled as a second generation model.
- No database or Alembic migration.
- No deployment, Provider enablement, customer traffic or production mutation.
- #595 traffic selection semantics and tests remain intact.

---

### Task 1: Strict capability schema, expectations and compatibility evaluator

**Files:**
- Create: `backend/app/services/provider_runtime/runtime_capabilities.py`
- Create: `backend/tests/test_provider_runtime_capabilities.py`

**Interfaces:**
- Produces: `RuntimeCapabilityExpectations.from_env() -> RuntimeCapabilityExpectations`
- Produces: `parse_capability_manifest(raw: bytes | str) -> RuntimeCapabilityManifest`
- Produces: `evaluate_capability_manifest(manifest, expectations) -> CapabilityProbeResult`
- Produces: `probe_private_ai_runtime_capabilities(...) -> CapabilityProbeResult`
- Produces: `CapabilityProbeResult.safe_summary() -> dict[str, object]`

- [ ] **Step 1: Write the failing parser and compatibility tests**

Add tests for a valid manifest, duplicate key, unknown field, secret-like key, malformed path, boolean dimension, unsupported schema, Runtime ID/version mismatch, generation model/contract mismatch, embedding model/dimension mismatch, missing reranker, wrong alias, and `not_ready` reason codes.

```python
def test_valid_manifest_matches_exact_expectations():
    result = evaluate_capability_manifest(
        parse_capability_manifest(json.dumps(valid_manifest()).encode()),
        expectations(),
    )
    assert result.ready is True
    assert result.reason_codes == ()
    assert result.safe_summary()["generation"]["model"] == "nexus-gemma4-e4b:latest"


def test_embedding_dimension_mismatch_fails_closed():
    payload = valid_manifest()
    payload["retrieval"]["embedding_dimension"] = 768
    result = evaluate_capability_manifest(parse_capability_manifest(json.dumps(payload)), expectations())
    assert result.ready is False
    assert result.reason_codes == ("capability_embedding_dimension_mismatch",)
```

- [ ] **Step 2: Run RED**

Run:

```bash
PYTHONPATH=backend pytest -q backend/tests/test_provider_runtime_capabilities.py
```

Expected: collection/import failure because `runtime_capabilities` does not exist.

- [ ] **Step 3: Implement strict immutable models and parser**

Use frozen dataclasses. Reject duplicate keys with `json.loads(..., object_pairs_hook=...)`. Validate exact key sets recursively. Reject keys whose normalized name contains `token`, `authorization`, `password`, `credential`, `secret`, `api_key`, `base_url` or `endpoint_url`.

```python
CAPABILITY_SCHEMA = "nexus.ai_runtime.capabilities.v1"
MAX_CAPABILITY_BYTES = 32 * 1024

@dataclass(frozen=True)
class CapabilityProbeResult:
    ready: bool
    reason_codes: tuple[str, ...]
    manifest: RuntimeCapabilityManifest | None = None

    def safe_summary(self) -> dict[str, object]:
        ...
```

- [ ] **Step 4: Implement exact expectation loading and comparison**

Require all generation and retrieval expectations. Parse dimension as a non-boolean integer. Return bounded reason codes in deterministic order; never include expected or actual secret/config sources in errors.

- [ ] **Step 5: Implement bounded no-redirect HTTP probe**

Use a relative path joined to the configured Runtime origin, a custom `HTTPRedirectHandler` that rejects redirects, `Accept: application/json`, a file-loaded bearer token and a limited read of `MAX_CAPABILITY_BYTES + 1`.

- [ ] **Step 6: Run GREEN and refactor**

```bash
PYTHONPATH=backend pytest -q backend/tests/test_provider_runtime_capabilities.py
```

Expected: all Task 1 tests pass with no warnings.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/provider_runtime/runtime_capabilities.py backend/tests/test_provider_runtime_capabilities.py
git commit -m "feat(provider): add strict AI Runtime capability contract"
```

---

### Task 2: Authenticated read-only Runtime capability endpoint

**Files:**
- Create: `infra/private_ai_runtime/__init__.py`
- Create: `infra/private_ai_runtime/capability_api.py`
- Create: `infra/private_ai_runtime/capability_manifest.example.json`
- Create: `backend/tests/test_private_ai_runtime_capability_endpoint.py`

**Interfaces:**
- Consumes: `parse_capability_manifest()` and `CapabilityManifestError`
- Produces: `create_capability_router(manifest_file: Path, token_file: Path) -> APIRouter`
- Route: `GET /v1/capabilities`

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_capability_endpoint_requires_bearer_token(tmp_path):
    client = client_for_manifest(tmp_path, valid_manifest(), token="test-token")
    response = client.get("/v1/capabilities")
    assert response.status_code == 401
    assert response.json() == {"detail": {"reason_code": "capability_unauthorized"}}


def test_capability_endpoint_serves_only_valid_safe_manifest(tmp_path):
    client = client_for_manifest(tmp_path, valid_manifest(), token="test-token")
    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["schema"] == "nexus.ai_runtime.capabilities.v1"
    assert "token" not in response.text.lower()
```

Cover wrong token, missing token file, duplicate-key manifest, oversized file and malformed manifest. Failure responses contain only bounded reason codes.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend:. pytest -q backend/tests/test_private_ai_runtime_capability_endpoint.py
```

Expected: import failure because the endpoint package does not exist.

- [ ] **Step 3: Implement router**

Read the token file and compare using `secrets.compare_digest`. Parse the manifest through Task 1. Return generic `401` or `503` errors without exception details. Set `Cache-Control: no-store`.

- [ ] **Step 4: Add safe example manifest**

The example contains the exact Work Item generation identity and bounded representative retrieval/voice values. It contains no address, credential, token or customer data and is explicitly non-deployment evidence.

- [ ] **Step 5: Run GREEN and commit**

```bash
PYTHONPATH=backend:. pytest -q backend/tests/test_private_ai_runtime_capability_endpoint.py
```

```bash
git add infra/private_ai_runtime backend/tests/test_private_ai_runtime_capability_endpoint.py
git commit -m "feat(runtime): expose authenticated capability manifest"
```

---

### Task 3: Verified production adapter and Provider fail-closed gate

**Files:**
- Create: `backend/app/services/provider_runtime/adapters/capability_verified_private_ai_runtime.py`
- Modify: `backend/app/services/provider_runtime/__init__.py`
- Create: `backend/tests/test_provider_runtime_capability_gate.py`
- Modify: `backend/tests/test_provider_runtime_router.py`

**Interfaces:**
- Consumes: Task 1 probe/evaluator
- Produces: `CapabilityVerifiedPrivateAIRuntimeAdapter`
- Registry: `private_ai_runtime` resolves only the verified adapter

- [ ] **Step 1: Write failing gate tests**

Cover:

- missing expectations suppress the underlying generation call;
- incompatible manifest suppresses generation and returns `fallback_allowed=False`;
- successful probe invokes generation exactly once;
- safe summary contains bounded `runtime_capability` evidence and no URL/token;
- active adapter uses `PRIVATE_AI_RUNTIME_GENERATION_MODEL`;
- conflicting legacy direct/RAG model variables fail closed;
- #595 control path does not trigger a capability probe or Runtime call.

```python
@pytest.mark.asyncio
async def test_capability_mismatch_blocks_generation(monkeypatch):
    adapter = verified_adapter(probe_result=not_ready("capability_generation_model_mismatch"))
    result = await adapter.generate(Mock(), request())
    assert result.ok is False
    assert result.error_code == "capability_generation_model_mismatch"
    assert result.fallback_allowed is False
    assert adapter.generation_calls == 0
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_provider_runtime_capability_gate.py \
  backend/tests/test_provider_runtime_router.py
```

Expected: import/registration assertions fail because the verified adapter is absent.

- [ ] **Step 3: Implement verified adapter**

Subclass `PrivateAIRuntimeAdapter`. Override active generation-model configuration, reject conflicting legacy model variables, probe before `super().generate()`, and attach only `CapabilityProbeResult.safe_summary()`.

- [ ] **Step 4: Register verified adapter lazily**

Change only the lazy bootstrap factory in `provider_runtime/__init__.py`; preserve no import-time side effects.

- [ ] **Step 5: Preserve #595 semantics**

The Router continues to decide control/shadow/canary/kill-switch before resolving/calling the adapter. Add a regression assertion that a control path creates neither adapter call nor probe call.

- [ ] **Step 6: Run GREEN and commit**

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_provider_runtime_capability_gate.py \
  backend/tests/test_provider_runtime_router.py \
  backend/tests/test_provider_runtime_private_ai_runtime_adapter.py
```

```bash
git add backend/app/services/provider_runtime backend/tests/test_provider_runtime_capability_gate.py backend/tests/test_provider_runtime_router.py
git commit -m "fix(provider): gate Runtime generation on capability identity"
```

---

### Task 4: Admin evidence, candidate config, smoke, CI and documentation

**Files:**
- Modify: `backend/app/services/provider_runtime_status.py`
- Modify: `backend/app/api/admin_provider_runtime.py`
- Modify: `backend/tests/test_provider_runtime_status.py`
- Modify: `backend/tests/test_admin_provider_runtime_routing_api.py`
- Modify: `backend/scripts/smoke_private_ai_runtime.py`
- Modify: `scripts/smoke/warm_private_ai_runtime.py`
- Modify: `deploy/.env.candidate.example`
- Modify: `deploy/.env.prod.example`
- Modify: `.github/workflows/provider-runtime-gate.yml`
- Modify: `docs/ops/PRIVATE_AI_RUNTIME_ROLLOUT_RUNBOOK.md`
- Create: `docs/engineering/private-ai-runtime-capability-contract.md`

**Interfaces:**
- Admin status exposes expected identity plus cached probe summary only.
- Privileged read-only endpoint refreshes the probe and returns bounded evidence.
- Smoke command exits nonzero for every not-ready reason.

- [ ] **Step 1: Write failing Admin/config/smoke tests**

Assert:

- Admin responses contain no Runtime origin/token/path-to-token;
- expected and actual capability identities are bounded;
- a probe mismatch makes status not ready;
- candidate/prod templates contain the new required expectation keys;
- active templates, smoke scripts and active runbook sections contain no `qwen2.5:3b` or `qwen3:4b` generation defaults;
- workflow runs all new tests.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend:. pytest -q \
  backend/tests/test_provider_runtime_status.py \
  backend/tests/test_admin_provider_runtime_routing_api.py \
  backend/tests/test_candidate_compose_contract.py
```

Expected: missing capability evidence/config assertions fail.

- [ ] **Step 3: Add bounded status and privileged probe response**

Keep ordinary status non-mutating. Use only cached probe state there. A privileged explicit probe route may perform the read-only upstream GET.

- [ ] **Step 4: Migrate configuration and smoke**

Add `PRIVATE_AI_RUNTIME_GENERATION_MODEL=nexus-gemma4-e4b:latest` and the exact expectation keys. Leave unknown candidate-specific Runtime version, embedding dimension and collection alias empty so the application fails closed until reviewed values are supplied. Remove active stale Qwen generation defaults; historical research statements remain historical and are not runtime authority.

- [ ] **Step 5: Update CI and engineering/runbook documentation**

Document token rotation without token values or internal addresses. The order is: rotate root-managed token files on both sides, restart/reload endpoint, run bounded probe, verify old token rejected, then revoke old secret.

- [ ] **Step 6: Run focused GREEN**

```bash
PYTHONPATH=backend:. pytest -q \
  backend/tests/test_provider_runtime_capabilities.py \
  backend/tests/test_private_ai_runtime_capability_endpoint.py \
  backend/tests/test_provider_runtime_capability_gate.py \
  backend/tests/test_provider_runtime_router.py \
  backend/tests/test_provider_runtime_private_ai_runtime_adapter.py \
  backend/tests/test_provider_runtime_status.py \
  backend/tests/test_admin_provider_runtime_routing_api.py \
  backend/tests/test_candidate_compose_contract.py
```

- [ ] **Step 7: Run repository gates**

```bash
PYTHONPATH=backend pytest -q backend/tests/test_provider_runtime_*.py backend/tests/test_admin_provider_runtime_routing_api.py
python scripts/ci/check_artifact_for_secrets.py --help
```

Then rely on exact-head GitHub workflows for backend, Provider Runtime, security, migration, integration and full regression evidence.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/provider_runtime_status.py backend/app/api/admin_provider_runtime.py backend/tests \
  backend/scripts/smoke_private_ai_runtime.py scripts/smoke/warm_private_ai_runtime.py deploy \
  .github/workflows/provider-runtime-gate.yml docs/ops/PRIVATE_AI_RUNTIME_ROLLOUT_RUNBOOK.md \
  docs/engineering/private-ai-runtime-capability-contract.md
git commit -m "docs(provider): bind candidate to Runtime capabilities"
```

---

### Task 5: Exact-head review and completion gate

**Files:**
- Review all changed files against #586 and the design document.

- [ ] Re-read #595 head and reconcile the stack if it changed.
- [ ] Compare actual changed resources with the Claim and PR manifest.
- [ ] Run all focused tests on the exact head.
- [ ] Confirm every new behavior had a witnessed RED failure before implementation.
- [ ] Confirm no unresolved Critical/Important review findings.
- [ ] Confirm no unresolved CodeQL/security thread on the stack.
- [ ] Confirm all exact-head workflows complete successfully.
- [ ] Update the PR with exact test evidence, rollback and unverified items.
- [ ] Move #586 to In Review only after implementation evidence is current.
- [ ] Do not merge #586 before #595 is accepted and merged or the stack is safely rebased onto then-current main.
- [ ] Production posture remains `NO_GO` until #533 records a separate exact-candidate decision.
