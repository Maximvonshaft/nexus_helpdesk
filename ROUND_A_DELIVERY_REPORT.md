# NexusDesk × OpenClaw Round A 交付报告

## 1. 本轮目标

Round A focuses on proving P0 functional chains with safe, repeatable smoke tests and deterministic OpenClaw mock fixtures. It does not introduce SaaS tenant migration, production channel sending, or large product refactors.

## 2. 修改文件清单

| 文件 | 类型 | 目的 | 对应链路 |
|---|---|---|---|
| `scripts/smoke/_lib.sh` | 脚本 | shared smoke helpers, env parsing, PASS/FAIL/SKIP | all |
| `scripts/smoke/smoke_all_round_a.sh` | 脚本 | aggregate Round A smoke runner | all |
| `scripts/smoke/smoke_e2e_integration_task.sh` | 脚本 | integration task creation/idempotency check | integration task |
| `scripts/smoke/smoke_e2e_openclaw_inbound_event.sh` | 脚本 | OpenClaw inbound event fixture coverage | inbound event |
| `scripts/smoke/smoke_e2e_transcript_sync.sh` | 脚本 | transcript fixture/idempotency coverage | transcript sync |
| `scripts/smoke/smoke_e2e_same_route_reply.sh` | 脚本 | mock same-route reply proof | same-route reply |
| `scripts/smoke/smoke_e2e_outbound_safety.sh` | 脚本 | outbound safety behavioral assertions | outbound safety |
| `scripts/smoke/smoke_e2e_unresolved_event_replay.sh` | 脚本 | unresolved event fixture coverage | unresolved events |
| `scripts/smoke/smoke_e2e_attachment_persist.sh` | 脚本 | attachment fixture safety coverage | attachment evidence |
| `scripts/smoke/smoke_e2e_runtime_health.sh` | 脚本 | healthz/readyz/metrics policy probe | runtime health |
| `backend/scripts/mock_openclaw_server.py` | 代码 | deterministic OpenClaw mock server | OpenClaw mock |
| `backend/tests/fixtures/openclaw/*.json` | 测试夹具 | deterministic fixtures for route, transcript, events, attachments | OpenClaw mock |
| `backend/tests/test_outbound_safety.py` | 测试 | pytest coverage for safety gate | outbound safety |
| `.github/workflows/round-a-smoke.yml` | CI | compile/test/build/alembic/smoke workflow | GitHub Actions |
| `docs/e2e-smoke-runbook.md` | 文档 | smoke usage and safety rules | runbook |
| `docs/openclaw-mock-testing.md` | 文档 | mock server contract | OpenClaw mock |
| `docs/same-route-reply-proof.md` | 文档 | same-route proof policy | same-route reply |
| `docs/worker-healthcheck-policy.md` | 文档 | worker healthcheck policy | runtime health |

## 3. 新增 smoke scripts

The new scripts support `--dry-run`, `--api-url`, `--prefix`, and `--help` through `_lib.sh`. Default behavior is safe and mock-oriented.

## 4. OpenClaw mock 能力

The mock server supports:

- `conversation_get`
- `messages_read`
- `attachments_fetch`
- `events_poll`
- `events_wait`
- `messages_send`

It can simulate success, missing route failure, forced send failure, duplicate message IDs, incomplete events, and multiple attachment shapes.

## 5. same-route proof 增强

`smoke_e2e_same_route_reply.sh` verifies that a mock `messages_send` call preserves:

- `channel`
- `recipient`
- `accountId`
- `threadId`

It also verifies missing route fields return failure.

## 6. outbound safety 验证

`backend/tests/test_outbound_safety.py` and `smoke_e2e_outbound_safety.sh` cover:

- empty body block
- sensitive/internal content block
- no-evidence logistics claim review
- AI auto reply review
- safe manual reply allow

## 7. unresolved event replay/drop 验证

Round A adds unresolved event fixture coverage and documents that DB-level replay/drop requires staging/live mode. It does not expose unsafe production test APIs.

## 8. attachment persist 验证

Round A adds fixture coverage for metadata, base64, text, and private URL attachments. Full storage persistence remains a staging/live smoke item because it writes to storage and DB.

## 9. runtime health 验证

`smoke_e2e_runtime_health.sh` checks `/healthz`, `/readyz`, and safe `/metrics` policy statuses when pointed at a live API. Worker health policy is documented separately.

## 10. GitHub Actions 增强

`.github/workflows/round-a-smoke.yml` runs backend compile, pytest, Alembic, frontend typecheck/build, dry-run smoke, and mock-only smoke subset without real OpenClaw secrets.

## 11. 剩余风险

- Real OpenClaw Gateway/MCP live validation is still required.
- Real WhatsApp/Telegram/WebChat customer channel validation is still required in staging.
- DB-writing smoke scripts should run only in disposable staging/test environments.
- same-route proof currently validates mock contract, not a real channel delivery receipt.

## 12. 下一轮建议

1. Run Round A workflow in GitHub Actions and fix any CI failures.
2. Add a staging-only temporary database smoke suite for service-layer DB assertions.
3. Validate same-route reply against real OpenClaw staging Gateway/MCP.
4. Add route proof logging to production message dispatch if not already sufficient.
5. Add GitHub branch protection and make Round A smoke required before merging.
