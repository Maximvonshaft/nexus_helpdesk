# Codex App-Server Runtime v3 Runbook

Start with rollback default:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=python_cli_pool
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-private-model-runtime codex-app-server-bridge
```

Start v3 candidate:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver
export CODEX_APPSERVER_RUNTIME_ENABLED=true
export CODEX_APPSERVER_PERFORMANCE_PROFILE=webchat_fast
export CODEX_APPSERVER_MODEL=gpt-5.3-codex-spark
export CODEX_APPSERVER_REASONING_EFFORT=low
export CODEX_APPSERVER_SERVICE_TIER=priority
export CODEX_APPSERVER_MAX_CONCURRENCY=4
export CODEX_APPSERVER_QUEUE_TIMEOUT_MS=750
export CODEX_APPSERVER_REPLY_TIMEOUT_MS=8000
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-appserver-runtime codex-app-server-bridge
```

Health checks:

```bash
curl -fsS http://127.0.0.1:18810/healthz
curl -fsS http://127.0.0.1:18810/readyz
curl -fsS http://127.0.0.1:18794/readyz
```

Server validation after owner provides a controlled valid token:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver
export WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
export CODEX_APPSERVER_PERFORMANCE_PROFILE=webchat_fast
export CODEX_APPSERVER_MODEL=gpt-5.3-codex-spark
export CODEX_APPSERVER_REASONING_EFFORT=low
export CODEX_APPSERVER_SERVICE_TIER=priority
export CODEX_APPSERVER_MAX_CONCURRENCY=4
export CODEX_APPSERVER_QUEUE_TIMEOUT_MS=750
export CODEX_APPSERVER_REPLY_TIMEOUT_MS=8000
read -r NEXUS_CODEX_ACCESS_TOKEN < /run/nexus/owner-provided-valid-token
export NEXUS_CODEX_ACCESS_TOKEN
export CODEX_APP_SERVER_BRIDGE_URL=http://172.18.0.1:18794/reply
export CODEX_APPSERVER_SLA_READYZ_URL=http://172.18.0.1:18794/readyz
bash scripts/probe_codex_appserver_discovery.sh
bash scripts/probe_codex_appserver_runtime_v3_sla.sh
```

Use the real token value from the controlled server credential boundary; do not write it into shell history, docs, PRs, logs, or artifacts.

WebChat enablement requires both runtime config and DB rollout state:

```bash
export WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
# DB canary > 0 is also required before customer WebChat traffic routes to codex_app_server.
```

Canary remains 0 by default. Do not increase customer canary until discovery, dummy negative, valid-token positive, SLA, audit, and runtime log checks all pass.

Do not count rollback or fallback traffic as Codex v3 success. Only responses with `reply_source=codex_app_server` and backend `nexus_codex_appserver_runtime` count toward v3 validation.

## Pilot Runtime Profile

Default controlled-probe profile for this engineering candidate:

- `CODEX_APPSERVER_PERFORMANCE_PROFILE=webchat_fast`
- `CODEX_APPSERVER_MODEL=gpt-5.3-codex-spark`
- `CODEX_APPSERVER_REASONING_EFFORT=low`
- `CODEX_APPSERVER_SERVICE_TIER=priority`
- `CODEX_APPSERVER_MAX_CONCURRENCY=4`
- `CODEX_APPSERVER_QUEUE_TIMEOUT_MS=750`
- `CODEX_APPSERVER_REPLY_TIMEOUT_MS=8000`
- `CODEX_APPSERVER_THREAD_MODE=ephemeral`
- `CODEX_APPSERVER_WORK_DIR=/tmp/nexus-codex-runtime/webchat-workdir`
- Request-scoped OAuth through `account/login/start`
- `dynamicTools=[]`

The performance hardening changes are intentional:

- Use an isolated empty workdir instead of the application repo so Codex does not spend turn budget on irrelevant workspace context.
- Send `effort=low` for modern Codex models; OpenClaw maps `minimal` to `low` because modern models reject or retry on `minimal`.
- Send `serviceTier=priority` when supported for customer-facing latency.
- Use hard backpressure at concurrency 4 until c5 or c6 proves zero `codex_turn_timeout`.
- Keep queue timeout classified as `codex_queue_timeout` so 12-parallel overload does not become a false success or generic upstream error.

## Model Benchmarking

Server evidence shows `gpt-5.5` is not the low-latency WebChat candidate for this route: it still produced sequential `codex_turn_timeout` under the PR235 profile. The controlled-pilot candidate is now `gpt-5.3-codex-spark` with c4 backpressure.

Benchmark candidates are opt-in only:

- `CODEX_APPSERVER_MODEL=gpt-5.5`
- `CODEX_APPSERVER_MODEL=gpt-5.4-mini`
- `CODEX_APPSERVER_MODEL=gpt-5.3-codex-spark`

Do not move beyond c4 or change the pilot model without a fresh controlled valid-token probe, dummy negative gate, and SLA run.

Benchmark model/profile candidates on the server:

```bash
read -r NEXUS_CODEX_ACCESS_TOKEN < /run/nexus/owner-provided-valid-token
export NEXUS_CODEX_ACCESS_TOKEN
export CODEX_APP_SERVER_BRIDGE_URL=http://172.18.0.1:18794/reply
export CODEX_APPSERVER_SLA_READYZ_URL=http://172.18.0.1:18794/readyz
export CODEX_APPSERVER_SLA_RESTART_RUNTIME=true
export CODEX_APPSERVER_SLA_PROFILE_MATRIX='spark_webchat_fast_c4,gpt-5.3-codex-spark,low,priority,4,750,8000,4;spark_webchat_fast_c5,gpt-5.3-codex-spark,low,priority,5,750,8000,5;spark_webchat_fast_c6,gpt-5.3-codex-spark,low,priority,6,750,8000,6'
bash scripts/probe_codex_appserver_runtime_v3_sla.sh
```

The script reports `recommended_profile` only when a profile passes dummy safety, leakage scan, sequential 20, parallel 6, and acceptable parallel 12 behavior.

## Server Validation Snapshot

Validated PR image:

- Image: `nexusdesk/helpdesk:pr233-codex-v3-20260525T103424Z`
- SHA: `8ded81f5b63ccb6214b25d1cec21da939710ae9c`
- Runtime command profile: `CODEX_APPSERVER_COMMAND=/usr/local/lib/node_modules/@openclaw/codex/node_modules/.bin/codex`
- Runtime model: `CODEX_APPSERVER_MODEL=gpt-5.5`

Observed results:

- Direct 18810 valid-token probe passed.
- Dummy token negative passed with no assistant reply.
- 18794 `node_appserver` route passed for valid token and failed closed for dummy token.
- Controlled single WebChat request passed with `reply_source=codex_app_server`, `ai_generated=true`, and `intent=tracking_missing_number`.
- Restore ran after the controlled probe. Canary remains 0 by default.
- 6-parallel pilot SLA passed functionally with p95 about 9092 ms.
- 12-parallel SLA still had errors after tuning.

Current status: pilot-functional only. Production no-go remains for broad canary, high parallel traffic, p95 above 8 seconds, and 12-parallel errors.

PR235 follow-up server facts:

- Dummy token safety passed.
- Token leakage count was 0.
- SLA failure was dominated by `codex_turn_timeout`.
- Observed `gpt-5.5` PR235 profile: sequential 18/20, parallel_6 5/6, parallel_12 5/12 with 6 controlled `codex_queue_timeout` and 1 `codex_turn_timeout`.
- Recovered matrix showed `gpt-5.3-codex-spark` was the closest candidate: sequential 20/20 p95 about 3927 ms, parallel_6 5/6 with one `codex_turn_timeout`, parallel_12 controlled queue only.

Engineering conclusion: the bottleneck is terminal model latency inside the turn, not bridge routing or queue classification. `gpt-5.5` should not be used as the low-latency WebChat default. Use spark c4 as the controlled-pilot candidate unless c5 or c6 passes with no `codex_turn_timeout`.

## Production Decision Matrix

| Result | Decision |
| --- | --- |
| Dummy assistant success > 0 or token leakage > 0 | Not safe for controlled probe |
| Sequential 20 or selected pilot parallel phase has `codex_turn_timeout` | Safe for owner debugging only; not safe for pilot |
| spark c4 selected pilot phase is 4/4 with p95 <= 8000 ms, dummy/leakage pass, parallel_12 has only controlled `codex_queue_timeout` | Safe for controlled pilot discussion, not broad production |
| c5 or c6 selected pilot phase is 100% success with p95 <= 8000 ms and parallel_12 has no `codex_turn_timeout` | Candidate to raise pilot concurrency after owner review |
| sequential 20 is 20/20, selected pilot phase is 100% success p95 <= 8000 ms, parallel_12 has no unclassified/model/upstream generic errors, audit clean | Production-candidate for owner review only |
| Any fallback/rollback counted as v3 success | Invalid run |

## Current Production No-Go List

- Broad WebChat canary or DB canary above the approved pilot value.
- Selected pilot phase p95 above 8 seconds.
- Any `codex_turn_timeout` in sequential, selected pilot phase, or parallel_12.
- Any `codex_upstream_http_error`, `codex_model_error`, or unclassified runtime errors.
- Any dummy-token assistant reply or terminal successful model turn.
- Any token material in response bodies, headers, logs, audit payloads, or test artifacts.
- Any fallback or rollback response counted as v3 Codex success.
- Removing or disabling Python 18800 rollback.
