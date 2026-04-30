# NexusDesk Production Closure Remediation Spec

版本：v1  
适用分支：main  
目标 commit 基线：`29cac403cbb81c4671df01ec4fb39b5bbe125981`  
任务性质：生产闭环整改 / 最小颗粒度修复 / 不做大范围重构

---

## 0. 执行目标

本轮整改目标不是新增业务大功能，而是把 NexusDesk main 分支从“生产候选”推进到“可受控小规模试运行”。

必须锁死以下闭环：

1. Webchat 客户入口可用；
2. 客户消息能生成 Customer / Ticket / Conversation / Message / Event；
3. 客服回复必须经过 outbound safety gate；
4. 客户 polling 必须能看到客服回复；
5. worker / sync-daemon / event-daemon 必须有清晰健康状态；
6. OpenClaw Gateway 不只是“能连上”，而是具备分层可验证能力；
7. 生产配置与服务器实盘状态不能漂移；
8. 前端必须让客服看懂 block / review / confirm 原因。

---

## 1. 工程原则

### 1.1 本轮允许做的事

- 新增测试；
- 新增探针脚本；
- 小范围修复 API / service / runtime health；
- 小范围优化 Webchat safety UI；
- 小范围统一 readiness / signoff 逻辑；
- 小范围补充 worker / daemon heartbeat；
- 不改变现有核心业务模型；
- 不改变现有认证体系；
- 不引入大型新框架。

### 1.2 本轮禁止做的事

- 不要重构整个后端架构；
- 不要重写前端；
- 不要改数据库大模型，除非 heartbeat 字段或 schema 已经存在则复用；
- 不要开启默认真实 outbound dispatch；
- 不要把 `ENABLE_OUTBOUND_DISPATCH` 默认改成 true；
- 不要把 Webchat AI 从 `safe_ack` 改成 unrestricted AI；
- 不要绕过 outbound safety gate；
- 不要删除 legacy fallback frontend；
- 不要为了性能提前引入 WebSocket / SSE，本轮只做闭环验证。

---

## 2. 最小整改任务总览

| ID | 任务 | 优先级 | 类型 | 是否阻塞小规模试运行 |
|---|---|---|---|---|
| T01 | 新增 Webchat E2E 测试 | P0 | Test | 是 |
| T02 | 新增 Webchat Safety E2E 测试 | P0 | Test | 是 |
| T03 | 补 worker / sync-daemon / event-daemon heartbeat | P1 | Backend / Runtime | 是，针对无人值守 |
| T04 | Runtime Health API 分开展示三类 daemon 状态 | P1 | Backend API | 是，针对无人值守 |
| T05 | Runtime 前端页面分区展示 API / DB / Worker / Sync / Event / OpenClaw | P2 | Frontend | 否，但建议一起做 |
| T06 | 新增 OpenClaw full-route 分级 probe | P1 | Backend / Script | 是，针对 OpenClaw 试运行 |
| T07 | 新增 server drift audit 脚本 | P1 | DevOps Script | 是，针对正式部署 |
| T08 | 统一 readiness evaluator | P1 | Backend / Config | 否，但建议做 |
| T09 | 优化 Webchat safety review UI | P2 | Frontend | 否，但建议做 |
| T10 | Webchat admin conversations 分页保护 | P3 | Backend / Frontend | 否，后续做 |

---

# T01 — 新增 Webchat E2E 测试

## 目标

证明 Webchat 公开入口从客户进入到后台客服可见是完整闭环。

## 当前问题

Webchat 代码已经存在，但缺少完整 E2E 测试。当前不能证明：

- init 一定创建 Customer；
- init 一定创建 Ticket；
- init 一定创建 WebchatConversation；
- 客户消息一定写入 WebchatMessage；
- 客户消息一定同步到 TicketComment；
- admin 端一定能看到 thread；
- 客户 polling 一定能看到消息。

## 修改文件

新增：

- `backend/tests/test_webchat_e2e.py`

可能涉及但原则上不应大改：

- `backend/app/api/webchat.py`
- `backend/app/services/webchat_service.py`
- `backend/app/webchat_models.py`
- `backend/app/models.py`

## 测试函数

```python
def test_webchat_init_creates_customer_ticket_conversation_event():
    ...

def test_webchat_visitor_message_updates_ticket_and_is_pollable():
    ...

def test_webchat_admin_reply_is_visible_to_visitor_polling():
    ...
```

## 最小断言

### `test_webchat_init_creates_customer_ticket_conversation_event`

必须断言：

- response status 为 200；
- response 有 `conversation_id`；
- response 有 `visitor_token`；
- DB 中创建 1 条 `Customer`；
- DB 中创建 1 条 `Ticket`；
- DB 中创建 1 条 `WebchatConversation`；
- `WebchatConversation.ticket_id == Ticket.id`；
- `Ticket.source_channel == web_chat`；
- `Ticket.preferred_reply_channel == web_chat`；
- 至少创建 1 条 `TicketEvent`。

### `test_webchat_visitor_message_updates_ticket_and_is_pollable`

必须断言：

- POST message 返回 200；
- DB 中创建 visitor direction 的 `WebchatMessage`；
- DB 中创建 external `TicketComment`；
- `ticket.last_customer_message` 更新为客户消息；
- `ticket.customer_request` 更新为客户消息；
- 生成 `webchat.ai_reply` background job；
- GET polling messages 能看到 visitor message。

### `test_webchat_admin_reply_is_visible_to_visitor_polling`

必须断言：

- admin reply 返回 200；
- DB 中创建 agent direction 的 `WebchatMessage`；
- DB 中创建 external `TicketComment`；
- DB 中创建 `TicketOutboundMessage`；
- `TicketOutboundMessage.status == sent`；
- ticket 状态变成 `waiting_customer`；
- 客户 polling 能看到 agent reply。

## 验收命令

```bash
cd backend
pytest -q backend/tests/test_webchat_e2e.py
```

## 完成标准

- 新增测试全部通过；
- 不依赖真实 OpenClaw；
- 不依赖外部网络；
- 不依赖真实浏览器；
- 使用 SQLite test DB 即可；
- 不改变生产行为。

---

# T02 — 新增 Webchat Safety E2E 测试

## 目标

证明 Webchat 客服回复一定经过 outbound safety gate，并且 block / review / confirm 行为不会误写客户可见消息。

## 当前问题

`outbound_safety.py` 已有 deterministic tests，但 Webchat admin reply 链路缺少 E2E 断言。

## 修改文件

新增：

- `backend/tests/test_webchat_safety_e2e.py`

可能涉及：

- `backend/app/services/webchat_service.py`
- `backend/app/services/outbound_safety.py`

## 测试函数

```python
def test_webchat_admin_reply_blocks_internal_leak_without_customer_visible_write():
    ...

def test_webchat_admin_reply_review_required_for_logistics_claim_without_confirm():
    ...

def test_webchat_admin_reply_confirm_review_allows_reviewed_message():
    ...
```

## 最小断言

### internal leak block

输入示例：

```text
OpenClaw MCP tool call failed with bearer token and database_url
```

必须断言：

- API 返回 400；
- response detail 包含 `Outbound reply blocked by safety gate`；
- 不新增 agent `WebchatMessage`；
- 不新增 sent `TicketOutboundMessage`；
- 不新增 external customer-visible `TicketComment`。

### logistics claim review

输入示例：

```text
Your parcel will arrive tomorrow.
```

条件：

```json
{
  "has_fact_evidence": false,
  "confirm_review": false
}
```

必须断言：

- API 返回 409；
- response detail 包含 `requires human review`；
- 不新增 agent `WebchatMessage`；
- 不新增 sent `TicketOutboundMessage`。

### confirm review

输入示例：

```text
Your parcel will arrive tomorrow.
```

条件：

```json
{
  "has_fact_evidence": true,
  "confirm_review": true
}
```

必须断言：

- API 返回 200；
- 新增 agent `WebchatMessage`；
- 新增 sent `TicketOutboundMessage`；
- `safety_level` 或 safety payload 可追踪；
- 客户 polling 能看到该消息。

## 验收命令

```bash
cd backend
pytest -q backend/tests/test_webchat_safety_e2e.py
```

## 完成标准

- block 不产生客户可见消息；
- review 不产生客户可见消息；
- confirm 后行为可追踪；
- 不削弱现有 `test_outbound_safety.py`。

---

# T03 — 补 worker / sync-daemon / event-daemon heartbeat

## 目标

让 runtime health 能证明后台进程是否真的活着。

## 当前问题

- `event-daemon` 已写 `ServiceHeartbeat`；
- `worker` 没有写 `ServiceHeartbeat`；
- `sync-daemon` 没有写 `ServiceHeartbeat`；
- runtime 页面无法完整判断无人值守运行状态。

## 修改文件

- `backend/scripts/run_worker.py`
- `backend/scripts/run_openclaw_sync_daemon.py`
- `backend/scripts/run_openclaw_event_daemon.py`
- `backend/app/services/heartbeat_service.py`
- `backend/app/api/admin.py`
- `backend/app/schemas.py`

## 实现要求

### worker heartbeat

在 `run_worker.py` 每次 cycle 后写入：

```text
service_name = worker
instance_id = worker_id
status = ok / error
details_json = {
  processed,
  outbound_processed,
  background_jobs_processed,
  enable_outbound_dispatch
}
```

### sync daemon heartbeat

在 `run_openclaw_sync_daemon.py` 每次 cycle 后写入：

```text
service_name = openclaw_sync_daemon
instance_id = worker_id
status = ok / error
details_json = {
  processed
}
```

### event daemon heartbeat

保留现有逻辑，但字段命名要和 runtime health 对齐：

```text
service_name = openclaw_event_daemon
```

## 新增测试

新增：

- `backend/tests/test_runtime_heartbeat.py`

测试函数：

```python
def test_worker_run_once_writes_heartbeat():
    ...

def test_sync_daemon_run_once_writes_heartbeat():
    ...

def test_event_daemon_heartbeat_schema_is_compatible():
    ...
```

## 验收命令

```bash
cd backend
pytest -q backend/tests/test_runtime_heartbeat.py
```

## 完成标准

- worker heartbeat 可查；
- sync daemon heartbeat 可查；
- event daemon heartbeat 可查；
- 不影响 `--once` 模式；
- 异常时 heartbeat status 能写 error。

---

# T04 — Runtime Health API 分开展示三类后台进程状态

## 目标

让 `/api/admin/openclaw/runtime-health` 或新的 runtime endpoint 能明确展示：

- worker 是否活着；
- sync-daemon 是否活着；
- event-daemon 是否活着；
- stale backlog；
- pending / dead jobs；
- OpenClaw connectivity warnings。

## 当前问题

runtime health 中 `sync_daemon_last_seen_at` 实际来源偏向 `openclaw_event_daemon`，语义容易误导。

## 修改文件

- `backend/app/api/admin.py`
- `backend/app/schemas.py`
- `webapp/src/lib/types.ts`
- `webapp/src/lib/api.ts`
- `webapp/src/routes/runtime.tsx`

## API 输出建议

```json
{
  "worker": {
    "status": "ok",
    "last_seen_at": "...",
    "details": {}
  },
  "openclaw_sync_daemon": {
    "status": "ok",
    "last_seen_at": "...",
    "details": {}
  },
  "openclaw_event_daemon": {
    "status": "ok",
    "last_seen_at": "...",
    "details": {}
  },
  "queue": {
    "pending_jobs": 0,
    "dead_jobs": 0,
    "pending_outbound": 0,
    "dead_outbound": 0
  },
  "openclaw": {
    "stale_link_count": 0,
    "pending_sync_jobs": 0,
    "dead_sync_jobs": 0
  },
  "warnings": []
}
```

## 判断规则

- heartbeat 缺失：warning；
- heartbeat 超过 stale seconds：warning；
- dead jobs > 0：warning；
- stale link count > batch size：warning；
- dead outbound > 0：warning。

## 新增测试

继续放在：

- `backend/tests/test_runtime_heartbeat.py`

测试函数：

```python
def test_runtime_health_reports_worker_sync_event_separately():
    ...

def test_runtime_health_warns_when_worker_heartbeat_missing():
    ...

def test_runtime_health_warns_when_sync_daemon_stale():
    ...
```

## 验收命令

```bash
cd backend
pytest -q backend/tests/test_runtime_heartbeat.py
```

## 完成标准

- API 字段语义清楚；
- 前端类型同步；
- 旧字段如需保留，只能作为 backward compatible，不作为 UI 主展示。

---

# T05 — Runtime 前端页面分区展示运行状态

## 目标

让非技术主管能判断系统是否健康。

## 修改文件

- `webapp/src/routes/runtime.tsx`
- `webapp/src/lib/types.ts`
- `webapp/src/lib/format.ts`

## 页面必须分成 6 个区块

1. API / DB Health
2. Worker Health
3. OpenClaw Sync Daemon Health
4. OpenClaw Event Daemon Health
5. Queue / Job Backlog
6. OpenClaw Gateway Connectivity

## UI 文案要求

不要只显示技术字段。

不推荐：

```text
sync_daemon_last_seen_at
```

推荐：

```text
消息同步服务最近心跳
```

不推荐：

```text
dead_jobs
```

推荐：

```text
失败后台任务，需要主管处理
```

## 验收命令

```bash
cd webapp
npm run typecheck
npm run build
```

## 完成标准

- 普通客服无权限进入 runtime；
- 主管能看懂状态；
- warnings 明确展示；
- 页面不因为字段为空崩溃；
- 没有 TypeScript error。

---

# T06 — 新增 OpenClaw full-route 分级 probe

## 目标

把 OpenClaw 检查从“能 list conversation”升级为“分层确认真实能力”。

## 当前问题

当前 connectivity check 只验证：

- MCP client 能启动；
- conversations_list 可用；
- 可见 sample session。

但不能证明：

- transcript read 可用；
- same-route send 可用；
- attachment metadata 可用；
- Gateway 权限完整。

## 修改文件

- `backend/app/services/openclaw_runtime_service.py`
- `backend/app/schemas.py`
- `backend/app/api/admin.py`
- 可新增：`backend/scripts/probe_openclaw_full_route.py`

## 分层 probe 设计

### L1 — Bridge Start

检查：

- MCP client 是否能启动；
- deployment mode；
- transport；
- command；
- URL；
- token/password auth 是否配置。

### L2 — Conversation List

检查：

- conversations_list 是否可用；
- conversations_seen；
- sample_session_key。

### L3 — Transcript Read

如果有 sample_session_key：

- 尝试读取 transcript / conversation messages；
- 不要求一定有消息；
- 失败必须明确 warning。

### L4 — Same-route Send Readiness

本轮不要真实发送客户消息。

只做 dry-run readiness：

- 是否有 session_key；
- 是否有 channel/account/recipient；
- 是否满足 same-route send 前置条件；
- 不触发真实 dispatch。

### L5 — Attachment Metadata

如果 MCP 暴露 attachment metadata：

- 检查 metadata tool 是否可用；
- 不强制 fetch binary。

## API 输出建议

```json
{
  "level": "L2",
  "bridge_started": true,
  "conversations_tool_ok": true,
  "transcript_read_ok": false,
  "same_route_send_ready": false,
  "attachment_metadata_ok": false,
  "warnings": []
}
```

## 新增测试

新增：

- `backend/tests/test_openclaw_full_route_probe.py`

要求：

- mock MCP client；
- 测试 L1/L2/L3/L4/L5 成功与失败；
- 不访问真实 OpenClaw；
- 不访问网络。

## 验收命令

```bash
cd backend
pytest -q backend/tests/test_openclaw_full_route_probe.py
```

## 完成标准

- Runtime 页面能看到 full-route probe；
- 不真实发送消息；
- 失败原因可读；
- 不影响现有 connectivity-check。

---

# T07 — 新增 server drift audit 脚本

## 目标

防止服务器实盘状态与 GitHub main 漂移。

## 新增文件

- `scripts/deploy/audit_server_drift.sh`

## 检查内容

脚本必须检查：

1. 当前目录是否 Git repo；
2. 当前 branch；
3. 当前 commit；
4. working tree 是否 dirty；
5. `deploy/.env.prod` 是否存在；
6. `.env.prod` 是否被 Git tracked；
7. `data/` 是否存在；
8. `backend/uploads` 或挂载 uploads 是否存在；
9. 当前 Alembic head；
10. DB 当前 migration version；
11. Docker compose 文件是否存在；
12. compose 中 app / worker / sync-daemon / event-daemon 是否存在；
13. Docker image tag / container status；
14. 最近 backup 文件是否存在；
15. 是否存在 server-only override 文件。

## 输出格式

必须输出：

```text
[PASS] xxx
[WARN] xxx
[FAIL] xxx
```

最后输出 summary：

```json
{
  "pass": 10,
  "warn": 2,
  "fail": 1,
  "overall": "not_ready"
}
```

## 不允许做的事

- 不要自动删除文件；
- 不要自动 reset；
- 不要自动修改 `.env.prod`；
- 不要自动重启容器；
- 只读审计。

## 验收命令

```bash
bash scripts/deploy/audit_server_drift.sh
```

## 完成标准

- 无破坏性操作；
- root / non-root 都能读出大部分信息；
- 缺少 Docker 时给 WARN，不直接崩；
- 缺少 `.env.prod` 给 FAIL；
- dirty working tree 给 WARN 或 FAIL，取决于是否是 server-only 文件。

---

# T08 — 统一 readiness evaluator

## 目标

让 CLI readiness、admin production-readiness、signoff-checklist 采用同一套判断标准。

## 当前问题

当前存在三套类似逻辑：

- `settings.py` production hard validation；
- `backend/scripts/validate_production_readiness.py`；
- `backend/app/api/admin.py` 中 production-readiness / signoff-checklist。

逻辑方向一致，但标准不完全统一。

## 修改文件

新增：

- `backend/app/services/readiness_service.py`

修改：

- `backend/scripts/validate_production_readiness.py`
- `backend/app/api/admin.py`
- `backend/app/schemas.py`

## readiness_service.py 要求

提供：

```python
def evaluate_production_readiness(db: Session | None = None) -> dict:
    ...
```

输出：

```json
{
  "status": "ready | not_ready",
  "checks": {
    "postgres_configured": true,
    "database_connected": true,
    "secret_key_configured": true,
    "allowed_origins_configured": true,
    "webchat_origins_configured": true,
    "legacy_webchat_token_disabled": true,
    "dev_auth_disabled": true,
    "cli_fallback_disabled": true,
    "storage_ready": true,
    "metrics_config_valid": true,
    "openclaw_mode_valid": true
  },
  "warnings": [],
  "failures": []
}
```

## 判断规则

- production 下 SQLite 是 failure；
- production 下 localhost allowed origins 是 failure；
- production 下 legacy webchat token 是 failure；
- production 下 CLI fallback 是 failure；
- S3 缺变量是 failure；
- local storage 在 production 是 warning 或 failure，由当前模板策略决定；
- metrics enabled 但无 token 是 failure；
- OpenClaw remote_gateway 但无 URL/token/password 是 failure。

## 验收命令

```bash
cd backend
python scripts/validate_production_readiness.py
pytest -q backend/tests/test_production_readiness.py
```

## 完成标准

- script/API signoff 输出一致；
- CI 不被破坏；
- development 环境仍可运行；
- production 错误配置 fail fast。

---

# T09 — 优化 Webchat Safety Review UI

## 目标

让客服知道为什么不能发送，以及下一步怎么做。

## 当前问题

Webchat 页面已有：reply textarea、hasFactEvidence、confirmReview、Toast error，但安全门返回 block / review 时，前端展示不够结构化。

## 修改文件

- `webapp/src/routes/webchat.tsx`
- `webapp/src/components/ui/*` 如需要新增小组件
- `webapp/src/lib/types.ts`

## UI 行为要求

### block

当后端返回 400 且包含 safety detail：

```text
回复已被安全门阻断
原因：
- 检测到内部系统信息 / token / OpenClaw / MCP / traceback 等
处理建议：
请删除内部信息后重新发送。
```

不能显示“确认后继续发送”。

### review

当后端返回 409：

```text
该回复需要人工复核
原因：
- 涉及物流事实承诺，但缺少证据
处理建议：
1. 核对 tracking / POD / internal note；
2. 勾选“我已核对系统证据”；
3. 勾选“我确认继续发送”；
4. 再次发送。
```

### allow

正常发送后：

```text
Webchat 回复已发送，访客端可见。
```

## 验收命令

```bash
cd webapp
npm run typecheck
npm run build
```

## 完成标准

- block/review/allow 三种状态清楚；
- 不泄露后端 stack trace；
- 不显示 OpenClaw 内部细节给普通客服；
- 不破坏现有 Webchat inbox 功能。

---

# T10 — Webchat admin conversations 分页保护

## 目标

防止 Webchat conversation 列表在数据量上来后无分页拖慢。

## 当前问题

admin list conversations 默认 limit 50，最大 100，方向可接受，但前端没有分页体验。

## 修改文件

- `backend/app/api/webchat.py`
- `backend/app/services/webchat_service.py`
- `webapp/src/routes/webchat.tsx`
- `webapp/src/lib/api.ts`
- `webapp/src/lib/types.ts`

## 实现建议

本轮可暂缓。若执行，只做最小分页：

```text
GET /api/webchat/admin/conversations?limit=50&offset=0
```

返回：

```json
{
  "items": [],
  "limit": 50,
  "offset": 0,
  "has_more": true
}
```

## 优先级

P3，暂缓。

---

## 3. 总体验收命令

本轮全部任务完成后，必须运行：

```bash
# Backend deterministic tests
cd backend
pytest -q

# Production readiness
python scripts/validate_production_readiness.py

# Frontend typecheck and build
cd ../webapp
npm ci
npm run typecheck
npm run build

# Deployment scripts should be executable
cd ..
bash scripts/deploy/audit_server_drift.sh || true
```

如果完整 pytest 太慢，至少运行：

```bash
cd backend
pytest -q \
  backend/tests/test_outbound_safety.py \
  backend/tests/test_webchat_e2e.py \
  backend/tests/test_webchat_safety_e2e.py \
  backend/tests/test_runtime_heartbeat.py \
  backend/tests/test_openclaw_full_route_probe.py \
  backend/tests/test_production_readiness.py
```

---

## 4. Definition of Done

本轮整改完成必须满足：

- Webchat E2E 测试存在并通过；
- Webchat Safety E2E 测试存在并通过；
- worker heartbeat 存在；
- sync-daemon heartbeat 存在；
- event-daemon heartbeat 保持可用；
- runtime health API 分开展示 worker / sync / event；
- runtime 页面能让主管看懂后台服务状态；
- OpenClaw probe 至少分 L1 / L2 / L3；
- server drift audit 脚本存在；
- readiness script 与 admin signoff 不再标准漂移；
- `npm run build` 通过；
- backend CI 不退化；
- 不打开默认真实 outbound dispatch；
- 不削弱 outbound safety gate；
- 不引入真实 secret；
- 不提交 `.env.prod`。

---

## 5. 本轮交付物

工程师最终需要提交 PR：

```text
production-closure: lock webchat e2e, safety gates, runtime health, and deployment drift
```

PR 描述必须包含：

```text
## What changed
- ...

## Verification
- [ ] pytest ...
- [ ] npm run typecheck
- [ ] npm run build
- [ ] validate_production_readiness.py
- [ ] audit_server_drift.sh

## Risk
- ...

## Rollback
- ...
```

新增或修改文件清单：

```text
backend/tests/test_webchat_e2e.py
backend/tests/test_webchat_safety_e2e.py
backend/tests/test_runtime_heartbeat.py
backend/tests/test_openclaw_full_route_probe.py
backend/tests/test_production_readiness.py
backend/app/services/readiness_service.py
backend/app/services/openclaw_runtime_service.py
backend/app/api/admin.py
backend/scripts/run_worker.py
backend/scripts/run_openclaw_sync_daemon.py
scripts/deploy/audit_server_drift.sh
webapp/src/routes/runtime.tsx
webapp/src/routes/webchat.tsx
webapp/src/lib/types.ts
webapp/src/lib/api.ts
```

不允许出现：

```text
.env.prod
真实 token
真实 password
真实 OpenClaw secret
真实数据库密码
大范围无关重构
默认开启 ENABLE_OUTBOUND_DISPATCH=true
默认开启 WEBCHAT_AI_AUTO_REPLY_MODE=safe_ai
```

---

## 6. 最小执行顺序

必须按以下顺序执行：

```text
Step 1: T01 Webchat E2E
Step 2: T02 Webchat Safety E2E
Step 3: T03 + T04 Runtime heartbeat and API
Step 4: T05 Runtime frontend
Step 5: T06 OpenClaw full-route probe
Step 6: T07 Server drift audit
Step 7: T08 Readiness evaluator
Step 8: T09 Webchat safety UI
Step 9: Final verification
```

不要先做 T09 UI，不要先做 P3 性能，不要先做大重构。

---

## 7. 最终工程判断标准

完成本 spec 后，NexusDesk main 分支应达到：

```text
可以进入受控小规模试运行。
条件：
- Webchat 挂白名单客户网站；
- safe_ack 模式；
- 人工客服主导回复；
- worker/sync/event 有 heartbeat；
- runtime 页面可见健康状态；
- server drift audit 通过；
- OpenClaw Gateway 至少 L2/L3 probe 通过；
- 不开启未经验证的真实 outbound dispatch。
```

如果以上任一 P0 失败，不允许公开客户入口。
