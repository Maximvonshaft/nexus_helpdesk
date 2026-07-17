import type { BadgeTone } from '@/lib/types'

const textReplacements: Array<[RegExp, string]> = [
  [/ExternalChannel/gi, '会话服务'],
  [/MCP/gi, '消息桥接'],
  [/CLI/gi, '备用通道'],
  [/NexusDesk/gi, '客服工作台'],
  [/helpdesk/gi, '客服系统'],
  [/daemon/gi, '守护进程'],
  [/runtime/gi, '运行状态'],
  [/sign-?off/gi, '上线检查'],
  [/cursor/gi, '游标'],
  [/sync/gi, '同步'],
]

const displayTextLabels: Record<string, string> = {
  private_ai_runtime: '统一 AI Runtime',
  none: '无',
  tracking_status: '查件与物流状态',
  tracking_number: '运单号',
  routing_unavailable: '运营路由不可用',
  provider_unavailable: '外部服务暂不可用',
  operator_takeover: '客服主动接管',
  'customer requested a human': '客户要求转人工',
  'customer asked for a human': '客户要求转人工',
  'review evidence and reply': '核实证据后回复客户',
  'verify parcel evidence': '核实运单证据',
  'collect tracking number': '向客户收集运单号',
  'collect missing fields before customer-facing resolution': '补齐缺失信息后再向客户说明结果',
  'check latest speedaf evidence before quoting parcel status': '查询最新 Speedaf 证据后再说明物流状态',
  'review latest message and evidence before replying': '核实最新消息和证据后回复',
  'handle active customer handoff': '处理当前人工接管请求',
  'ai paused; review handoff and decide reply/resume': '自动回复已暂停，请核实接手原因并决定人工回复或恢复自动回复',
}

const valueLabels: Record<string, string> = {
  all: '全部',
  active: '启用中',
  inactive: '已停用',
  new: '新建',
  open: '处理中',
  pending_assignment: '待分配',
  pending_human: '待人工处理',
  in_progress: '处理中',
  waiting_customer: '待客户回复',
  waiting_internal: '等待内部处理',
  escalated: '已升级',
  resolved: '已解决',
  closed: '已关闭',
  canceled: '已取消',
  low: '低优先级',
  medium: '普通',
  high: '高优先级',
  urgent: '紧急',
  notice: '通知',
  delay: '延误',
  disruption: '异常',
  customs: '清关',
  customer: '客户',
  operator: '客服',
  both: '客户与客服',
  admin: '管理员',
  manager: '主管',
  lead: '组长',
  agent: '客服',
  auditor: '审计',
  info: '普通',
  warning: '提醒',
  critical: '紧急',
  whatsapp: 'WhatsApp',
  telegram: 'Telegram',
  sms: '短信',
  email: '邮件',
  web_chat: '网页聊天',
  healthy: '正常',
  degraded: '受限',
  offline: '离线',
  unknown: '未知',
  user: '客户',
  assistant: '智能助手',
  system: '系统',
  ai_active: '智能处理中',
  human_review_required: '待人工复核',
  human_owned: '人工处理中',
  ready_to_reply: '待发送回复',
  replied_to_customer: '已回复客户',
  reopened_by_customer: '客户再次来信',
  customer_replied: '客户已回复',
  waiting_reply: '待回复',
  no_conversation_state: '未标记',
}

const signoffLabels: Record<string, string> = {
  postgres_configured: '数据库已切换为 PostgreSQL',
  storage_configured: '附件存储已配置',
  metrics_enabled: '监控已启用',
  transport_configured: '消息通道已配置',
  event_driver_enabled: '事件消费已启用',
  sync_enabled: '消息同步已启用',
  bucket_configured: '对象存储已配置',
  queue_ready: '任务队列已就绪',
}

function normalizeValue(value?: string | null) {
  return String(value || '').trim().toLowerCase()
}

export function formatDateTime(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return String(value)
  }
}

export function sanitizeDisplayText(value?: string | number | boolean | null) {
  if (value === undefined || value === null || value === '') return '—'
  const source = String(value)
  const exact = displayTextLabels[source.trim().toLowerCase()]
  if (exact) return exact
  let text = source
  for (const [pattern, replacement] of textReplacements) {
    text = text.replace(pattern, replacement)
  }
  return text
}

export function labelize(value?: string | null) {
  if (!value) return '—'
  const normalized = normalizeValue(value)
  if (valueLabels[normalized]) return valueLabels[normalized]
  return sanitizeDisplayText(String(value).replaceAll('_', ' ').replace(/\w+/g, (word) => word.charAt(0).toUpperCase() + word.slice(1)));
}

export function marketLabel(marketCode?: string | null, countryCode?: string | null) {
  return sanitizeDisplayText(marketCode || countryCode || '全局')
}

export function statusTone(value?: string | null): BadgeTone {
  const normalized = normalizeValue(value)
  if (normalized === 'resolved' || normalized === 'closed') return 'success'
  if (normalized === 'waiting_customer') return 'warning'
  if (normalized === 'urgent') return 'danger'
  return 'default'
}

export function priorityTone(value?: string | null): BadgeTone {
  const normalized = normalizeValue(value)
  if (normalized === 'urgent') return 'danger'
  if (normalized === 'high') return 'warning'
  if (normalized === 'low') return 'success'
  return 'default'
}

export function severityTone(value?: string | null): BadgeTone {
  const normalized = normalizeValue(value)
  if (normalized === 'critical') return 'danger'
  if (normalized === 'warning') return 'warning'
  if (normalized === 'info') return 'success'
  return 'default'
}

export function healthTone(value?: string | null): BadgeTone {
  const normalized = normalizeValue(value)
  if (normalized === 'healthy') return 'success'
  if (normalized === 'degraded') return 'warning'
  if (normalized === 'offline') return 'danger'
  return 'default'
}

export function boolLabel(value?: boolean | null, truthy = '是', falsy = '否') {
  return value ? truthy : falsy
}

export function signoffLabel(value: string) {
  if (signoffLabels[value]) return signoffLabels[value]
  return sanitizeDisplayText(value.replaceAll('_', ' '))
}
