import type { AuthUser } from '@/lib/types'
import type { AccessRequirement } from '@/lib/rbac'
import { canAccess } from '@/lib/rbac'
import { routePermissionMap } from './routePermissionMap'

export type NavigationRisk = 'normal' | 'high'

export type NavigationItem = {
  to: string
  label: string
  group: string
  description: string
  access?: AccessRequirement
  roles?: string[]
  risk?: NavigationRisk
  attention?: 'runtime'
}

export const navigationGroups = [
  { label: '工作台', description: '客服每天进入系统后的主处理入口。' },
  { label: '工单中心', description: '按真实处理队列组织工单视图。' },
  { label: '客户/运单查询', description: 'Customer、Waybill、Phone 和 Caller ID 的一级查询入口。' },
  { label: '运营报表', description: '主管和运营管理视角的 SLA、队列和健康度入口。' },
  { label: '知识与自动化', description: '知识、助手人设、AI 策略、公告和 QA 训练入口。' },
  { label: '配置管理', description: '渠道、邮件、Provider、Integration 和 Speedaf API 配置入口。' },
  { label: '系统管理', description: '用户、权限、审计和发布就绪入口。' },
] as const

export const navigationItems: NavigationItem[] = [
  { to: '/', label: '今日工作台', group: '工作台', description: '今日待处理、SLA 风险、异常与优先动作。' },
  { to: '/webchat', label: 'WebChat', group: '工作台', description: '客户 WebChat 实时消息、AI 状态、转人工和接管处理。', access: routePermissionMap['/webchat'] },
  { to: '/webcall', label: 'WebCall', group: '工作台', description: '来电队列、语音会话、AI 摘要和人工接听。', access: routePermissionMap['/webcall'] },
  { to: '/email', label: 'Email', group: '工作台', description: '邮件队列、草稿、发送、失败重试和归档。', access: routePermissionMap['/email'] },

  { to: '/workspace', label: '我的工单', group: '工单中心', description: '当前账号负责或可见的工单处理台。', access: routePermissionMap['/workspace'] },
  { to: '/workspace?queue=unassigned', label: '待分配', group: '工单中心', description: '未分配、需主管或队列负责人处理的工单。', access: routePermissionMap['/workspace'] },
  { to: '/workspace?queue=escalated', label: '升级/异常', group: '工单中心', description: '升级、异常、SLA 风险和需人工判断的工单。', access: routePermissionMap['/workspace'] },
  { to: '/workspace?status=closed', label: '已关闭', group: '工单中心', description: '已闭环工单查询和复盘入口。', access: routePermissionMap['/workspace'] },
  { to: '/workspace?queue=follow-up', label: '回访任务', group: '工单中心', description: '需要回访、补充材料或二次确认的客户任务。', access: routePermissionMap['/workspace'] },

  { to: '/customer-waybill?tab=customer', label: 'Customer 360', group: '客户/运单查询', description: '客户资料、历史会话、历史工单、运单和风险提示。', access: routePermissionMap['/customer-waybill'] },
  { to: '/customer-waybill?tab=waybill', label: '运单查询', group: '客户/运单查询', description: '按 waybill/order/tracking number 查询订单与派送状态。', access: routePermissionMap['/customer-waybill'] },
  { to: '/customer-waybill?tab=phone', label: '手机号查询', group: '客户/运单查询', description: '按客户手机号查询关联客户、会话、工单和运单线索。', access: routePermissionMap['/customer-waybill'] },
  { to: '/customer-waybill?tab=caller', label: 'Caller ID 查询', group: '客户/运单查询', description: '按 WebCall caller ID 查询客户和通话关联工单。', access: routePermissionMap['/customer-waybill'] },
  { to: '/customer-waybill?tab=speedaf', label: 'Speedaf 操作', group: '客户/运单查询', description: 'Speedaf 催派、地址更新、取消运单等高风险动作的能力检查入口。', access: routePermissionMap['/customer-waybill'], risk: 'high' },

  { to: '/control-tower?view=sla', label: 'SLA 看板', group: '运营报表', description: 'SLA 风险、即将超时和处理优先级。', access: routePermissionMap['/control-tower'] },
  { to: '/control-tower?view=queue', label: '队列压力', group: '运营报表', description: '各渠道、团队和队列的积压压力。', access: routePermissionMap['/control-tower'] },
  { to: '/control-tower?view=agent', label: 'Agent 绩效', group: '运营报表', description: '客服处理效率、质量和未闭环工作量。', access: routePermissionMap['/control-tower'] },
  { to: '/control-tower?view=ai-handoff', label: 'AI / Handoff 质量', group: '运营报表', description: 'AI 命中、转人工、失败和复核质量。', access: routePermissionMap['/control-tower'] },
  { to: '/runtime', label: 'Provider / Runtime Health', group: '运营报表', description: 'Provider、Runtime、队列、dead jobs 和恢复动作。', access: routePermissionMap['/runtime'], risk: 'high', attention: 'runtime' },

  { to: '/knowledge-studio', label: '知识库', group: '知识与自动化', description: '知识草稿、发布、检索、冲突和证据。', access: routePermissionMap['/knowledge-studio'] },
  { to: '/persona-builder', label: '助手人设', group: '知识与自动化', description: '助手身份、人设模板、渠道口径和发布证据。', access: routePermissionMap['/persona-builder'] },
  { to: '/ai-control', label: 'AI 策略', group: '知识与自动化', description: 'AI 规则、策略、开关、回滚和治理。', access: routePermissionMap['/ai-control'], risk: 'high' },
  { to: '/bulletins', label: '公告口径', group: '知识与自动化', description: '客服统一公告、临时口径和客户话术。', access: routePermissionMap['/bulletins'] },
  { to: '/qa-training', label: 'QA Training', group: '知识与自动化', description: '质检样本、训练任务和知识缺口闭环。', access: routePermissionMap['/qa-training'] },

  { to: '/accounts', label: 'Channel Accounts', group: '配置管理', description: '渠道账号、发送线路和兜底策略。', access: routePermissionMap['/accounts'], risk: 'high' },
  { to: '/outbound-email', label: 'Outbound Email', group: '配置管理', description: 'SMTP host、port、username、secret、from address 和测试发送。', access: routePermissionMap['/outbound-email'], risk: 'high' },
  { to: '/provider-credentials', label: 'Provider Credentials', group: '配置管理', description: 'Provider/Codex 授权、token 托管、刷新和撤销。', access: routePermissionMap['/provider-credentials'], risk: 'high' },
  { to: '/control-plane?section=webhooks', label: 'Webhook / Integration', group: '配置管理', description: 'Integration、Webhook、Bridge 和外部系统接入治理。', access: routePermissionMap['/control-plane'], risk: 'high' },
  { to: '/accounts?section=speedaf-api', label: 'Speedaf API 配置', group: '配置管理', description: 'Speedaf API 账号、市场、权限和健康检查入口。', access: routePermissionMap['/accounts'], risk: 'high' },

  { to: '/users', label: 'Users', group: '系统管理', description: '员工账号、状态、角色和 capability override。', access: routePermissionMap['/users'], risk: 'high' },
  { to: '/security?view=roles', label: 'Roles & Permissions', group: '系统管理', description: '角色、capability、权限矩阵和最小授权审查。', access: routePermissionMap['/security'], risk: 'high' },
  { to: '/security?view=audit', label: 'Audit Logs', group: '系统管理', description: '管理员操作、权限变更和配置变更审计记录。', access: routePermissionMap['/security'] },
  { to: '/runtime?view=readiness', label: 'Release / Readiness', group: '系统管理', description: '发布前检查、构建证据、运行恢复和回滚准备。', access: routePermissionMap['/runtime'], risk: 'high', attention: 'runtime' },
  { to: '/webcall-ai-demo', label: 'WebCall AI Demo（Ops Only）', group: '系统管理', description: '内部 WebCall AI sandbox，仅 runtime.manage 可见。', access: routePermissionMap['/webcall-ai-demo'], risk: 'high' },
]

export const uniqueNavigationRoutes = Array.from(new Set(navigationItems.map((item) => item.to.split('?')[0] || item.to)))

export function getVisibleNavigation(user?: AuthUser | null) {
  return navigationItems.filter((item) => {
    if (item.roles?.length && !item.roles.includes(String(user?.role || '').toLowerCase())) return false
    if (item.access) return canAccess(user, item.access)
    return true
  })
}
