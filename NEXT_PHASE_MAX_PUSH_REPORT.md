# Next Phase Max Push Report

## 本轮目标
基于 Round20B 源码，忽略时间维度，按既定路线图中“最有杠杆”的事项继续往下推进，不再停留在 bug 修补层，而是把系统往 **单租户生产级 Agent 控制台** 方向再推一层。

本轮优先落地的是：
- 把“公告”之上的能力抽成真正的 **AI 配置层雏形**
- 让主前端 `webapp` 承载这层配置能力
- 保留当前单租户生产闭环，不打断现有客服/主管视角
- 交付一份明确的 **已完成 / 未完成** 清单，避免继续困在概念混杂里

## 本轮实际完成

### 1. AI 配置层雏形已落地
新增了两张核心表：
- `ai_config_resources`
- `ai_config_versions`

支持以下能力：
- 规则资源类型：`persona / knowledge / sop / policy`
- 草稿与线上已发布内容分离
- 发布版本号递增
- 发布历史留痕
- 指定历史版本一键回滚（通过重新发布形成新版本）

### 2. 后端接口已补齐
新增管理接口：
- `GET /api/admin/ai-configs`
- `POST /api/admin/ai-configs`
- `PATCH /api/admin/ai-configs/{id}`
- `POST /api/admin/ai-configs/{id}/publish`
- `GET /api/admin/ai-configs/{id}/versions`
- `POST /api/admin/ai-configs/{id}/rollback/{version}`

新增只读发布接口：
- `GET /api/lookups/ai-configs`

这意味着系统第一次具备了“**配置作为资源**”而不是“把所有规则塞进公告或代码里”的能力。

### 3. 主前端增加 AI 规则控制页
在 `webapp` 新增：
- `AI规则` 导航入口
- `智能助手规则与知识配置` 页面

页面支持：
- 按类型切换：人格 / 知识 / SOP / 执行边界
- 规则列表
- 草稿编辑
- JSON 配置内容维护
- 发布当前草稿
- 查看发布历史
- 回滚到指定版本
- 查看已发布预览

这一步非常关键：
**系统不再只是客服工作台，而是开始具备 AI 控制台的雏形。**

### 4. 权限边界保持稳定
AI 规则管理继续按最小权限收口：
- 主管 / 管理员：可管理
- 一线客服 / 组长：不可管理

没有为了“新能力”破坏现有前后端权限约束。

### 5. 初始化种子数据已扩展
`init_dev_db.py` 现在会同时种出 4 类默认 AI 配置资源：
- Persona
- Knowledge
- SOP
- Policy

并且会自动发布第一版，便于演示与联调。

## 本轮验证结果
- `npm ci && npm run build`：通过
- `python backend/scripts/smoke_verify_next_phase.py`：通过
- `pytest`：`43 passed`
- `init_dev_db.py` 实测：
  - `tickets = 1`
  - `market_bulletins = 1`
  - `ai_config_resources = 4`
  - `ai_config_versions = 4`

## 这意味着什么
当前系统已经从：
- 客服工单系统 + 公告 + OpenClaw 集成

推进到了：
- 客服工单系统 + 公告 + OpenClaw 集成 + **AI 配置控制层雏形**

这是路线上的一个实质性拐点。

---

# 对照总清单：哪些已经做了，哪些还没做

## A. 产品层
### 已做
- `webapp` 持续强化为主前端
- 新增 AI 规则入口与控制页
- 角色化导航继续成立
- legacy frontend 保持 fallback 定位

### 未做完
- 尚未正式把 legacy frontend 降级为只读/应急模式并在部署层强制主前端唯一化
- 产品术语仍未彻底统一成一套正式对外/对内字典
- 高风险动作审批 UI 还没有产品化

## B. 业务层
### 已做
- AI 配置层开始支持 SOP / Policy 资源
- 公告与 AI 配置层完成概念拆分

### 未做完
- case type 体系仍不完整
- SLA/超时/升级规则未产品化为配置层资源
- 附件/证据闭环虽然存在，但还没形成面向业务的媒体证据控制台
- 主管运营视图仍偏运行态，不是完整业务经营视图

## C. AI 配置层
### 已做
- Persona / Knowledge / SOP / Policy 四类资源雏形
- 草稿/发布/版本/回滚
- 管理端页面
- 只读发布查询接口

### 未做完
- 还没有审批流
- 还没有生效范围组合规则（目前只到 scope_type / scope_value / market_id）
- 还没有真正的配置 diff 对比
- 还没有“发布说明/审批人/回滚原因”完整治理链
- 还没有把配置真正接入 AI 推理链做细粒度执行

## D. OpenClaw / MCP 层
### 已做（之前已有，本轮未重点改）
- MCP-first 方向已建立
- 会话/工单/同步链路已存在

### 本轮未推进
- 渠道健康控制台没有新扩展
- fallback 原因分析未做深
- 工具治理/审批后执行仍未产品化
- 媒体证据落地与预览控制台未做

## E. 平台层
### 已做（之前已有，本轮未重点改）
- 基础 health / metrics / readiness / signoff 还在

### 未做完
- PostgreSQL 正式 staging rehearsal 还没形成签字级报告
- 对象存储治理未完成
- tracing / alerting / centralized logging 仍不完整
- runbook / rollback / DR 文档还不够平台级

## F. 多租户层
### 已做
- 暂无正式实现

### 未做
- 没有 `tenant_id`
- 没有 Tenant 模型
- 没有 tenant-scoped auth
- 没有 tenant-scoped config / storage / queue / secrets
- 没有平台管理员 / 租户管理员边界

## G. SaaS 层
### 已做
- 暂无正式实现

### 未做
- 没有开租户流程
- 没有计费/配额模型
- 没有 SaaS 管理台
- 没有企业级 onboarding 流程

---

# 当前最准确的定位
**这套系统现在已经不是“客服工单 demo”，也还不是“多租户 SaaS”。**

它现在最准确的定位是：

> **单租户、接近生产可用、以客服与运营工单为核心，并开始具备 AI 配置控制层的 agent-native customer operations runtime。**

---

# 现在还差什么，才能真正跨到下一层
按价值排序，下一步最该继续做的是：

1. **把 AI 配置层真正接入执行链**
   - 不是只存配置，而是让 Persona / Knowledge / SOP / Policy 参与 AI 决策与回复生成

2. **做发布治理**
   - 审批、发布人、发布单、回滚原因、变更 diff

3. **把 OpenClaw 做成业务控制台**
   - 渠道健康、同步积压、失败原因、人工接管、媒体证据

4. **把主前端唯一化做完**
   - legacy frontend 彻底降级，不再与主产品叙事并行

5. **再做 tenant-ready**
   - 先设计租户边界，不要急着直接多租户落表

---

# 最终结论
本轮不是“继续修小问题”，而是已经把系统往路线图中的第二层——**AI 配置控制台**——实质性推进了一步。

但距离完整路线图仍然还有清晰的剩余项，尤其是：
- 配置层接入执行链
- OpenClaw 产品化控制台
- 平台级观测与治理
- tenant-ready
- 多租户与 SaaS 化

换句话说：
**这次已经把“路”铺出来了，但还没有把整条高速公路修完。**
