import type { BadgeTone } from '@/lib/types'

const textReplacements: Array<[RegExp, string]> = [
  [/OpenClaw/gi, '会话服务'],
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
  let text = String(value)
  for (const [pattern, replacement] of textReplacements) {
    text = text.replace(pattern, replacement)
  }
  return text
}

export function labelize(value?: string | null) {
  if (!value) return '—'
  const normalized = normalizeValue(value)
  if (valueLabels[normalized]) return valueLabels[normalized]
  return sanitizeDisplayText(String(value).replaceAll('_', ' ').replace(/\w+/g, (word) => word.charAt(0).toUpperCase() + word.slice(1)))
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
