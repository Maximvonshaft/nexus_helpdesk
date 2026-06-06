# Codex Direct Latency Baseline Runbook

This runbook supports PR #406: measuring Codex Direct latency without changing the WebChat Fast production reply path.

## Scope

This baseline is intentionally conservative.

It does not:

- change provider routing;
- enable canned deterministic replies as the main path;
- change Speedaf API behavior;
- change tracking fact source;
- create tickets, conversations, customer messages, or Speedaf write actions in direct mode.

It does:

- read `provider_runtime_audit_logs` in audit mode;
- optionally call `CodexDirectAdapter.generate()` with synthetic `ProviderRequest` payloads in direct mode;
- emit JSONL raw samples, summary JSON, and a Markdown SLO report.

## Recommended production-safe first run

Run from repository root on a host that can access the production database:

```bash
PYTHONPATH=backend python scripts/bench_codex_direct_webchat_fast.py \
  --mode audit \
  --since-minutes 1440 \
  --limit 500 \
  --profile-label observed_production_audit \
  --output-dir artifacts/codex_direct_latency \
  --label codex_audit_$(date -u +%Y%m%d_%H%M%S)
```

Expected outputs:

```text
artifacts/codex_direct_latency/<label>.jsonl
artifacts/codex_direct_latency/<label>.summary.json
artifacts/codex_direct_latency/<label>.slo.md
```

## Synthetic direct adapter probe

Use this only on a runtime host with Codex Direct enabled and authenticated.

```bash
PYTHONPATH=backend python scripts/bench_codex_direct_webchat_fast.py \
  --mode direct \
  --runs 20 \
  --concurrency 1 \
  --tracking-fact-present \
  --prompt-size-chars 1800 \
  --profile-label direct_tracking_1800_c1 \
  --output-dir artifacts/codex_direct_latency \
  --label codex_direct_tracking_1800_c1_$(date -u +%Y%m%d_%H%M%S)
```

Concurrency probe:

```bash
PYTHONPATH=backend python scripts/bench_codex_direct_webchat_fast.py \
  --mode direct \
  --runs 20 \
  --concurrency 2 \
  --tracking-fact-present \
  --prompt-size-chars 1800 \
  --profile-label direct_tracking_1800_c2 \
  --output-dir artifacts/codex_direct_latency \
  --label codex_direct_tracking_1800_c2_$(date -u +%Y%m%d_%H%M%S)
```

Prompt-size sweep:

```bash
for size in 1200 1800 3000 6000; do
  PYTHONPATH=backend python scripts/bench_codex_direct_webchat_fast.py \
    --mode direct \
    --runs 10 \
    --concurrency 1 \
    --tracking-fact-present \
    --prompt-size-chars "$size" \
    --profile-label "direct_tracking_${size}_c1" \
    --output-dir artifacts/codex_direct_latency \
    --label "codex_direct_tracking_${size}_c1_$(date -u +%Y%m%d_%H%M%S)"
done
```

## Decision rule

Use the generated `.slo.md` report.

- If Codex total p95 < 8s: proceed to prompt compression.
- If Codex total p95 is 8-15s: proceed to prompt compression plus warmup/health probe.
- If Codex total p95 > 15s or timeout rate is elevated: prioritize health-aware AI fallback provider and Codex worker shadow test.

## Validation

```bash
PYTHONPATH=backend python -m pytest -q backend/tests/test_codex_direct_latency_baseline.py
python -m py_compile scripts/bench_codex_direct_webchat_fast.py
```
