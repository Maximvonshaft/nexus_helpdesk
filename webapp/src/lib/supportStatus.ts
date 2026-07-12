import type { BadgeTone, SupportConversation } from '@/lib/types'

export type OperationalPresentation = {
  tone: BadgeTone
  label: string
  detail?: string | null
}

type RuntimePresentationInput = {
  isLoading: boolean
  isError: boolean
  ok?: boolean | null
  warnings?: readonly unknown[] | null
}

const VERIFIED_HEALTH = new Set([
  'connected',
  'healthy',
  'ok',
  'online',
  'pass',
  'ready',
  'success',
])

const DEGRADED_HEALTH = new Set([
  'checking',
  'connecting',
  'degraded',
  'idle',
  'pending',
  'qr_pending',
  'reconnecting',
  'review',
  'unknown',
  'warning',
])

const FAILED_HEALTH = new Set([
  'blocked',
  'dead',
  'disabled',
  'disconnected',
  'error',
  'failed',
  'not_ok',
  'not_ready',
  'offline',
  'unavailable',
  'unhealthy',
])

const REQUEST_PENDING = new Set([
  'accepted',
  'pending',
  'processing',
  'queued',
  'requested',
  'submitted',
])

const REQUEST_FAILED = new Set([
  'blocked',
  'error',
  'failed',
  'repair_required',
  'rejected',
])

const VERIFIED_OUTCOME = new Set([
  'business_result_confirmed',
  'operational_completed',
])

function normalizeStatus(value: string | null | undefined): string {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
}

export function healthPresentation(value: string | null | undefined): OperationalPresentation {
  const normalized = normalizeStatus(value)
  const visible = String(value ?? '').trim()

  if (FAILED_HEALTH.has(normalized)) {
    return { tone: 'danger', label: visible || '不可用' }
  }
  if (VERIFIED_HEALTH.has(normalized)) {
    return { tone: 'success', label: visible || '正常' }
  }
  if (!normalized || DEGRADED_HEALTH.has(normalized)) {
    return { tone: 'warning', label: visible || '状态未知' }
  }
  return { tone: 'warning', label: visible }
}

export function knowledgeStatusPresentation(value: string | null | undefined): OperationalPresentation {
  const normalized = normalizeStatus(value)
  if (normalized === 'active' || normalized === 'published') return { tone: 'success', label: '已上线' }
  if (normalized === 'draft') return { tone: 'warning', label: '草稿' }
  if (normalized === 'archived') return { tone: 'default', label: '已归档' }
  return { tone: 'warning', label: String(value ?? '').trim() || '状态未知' }
}

export function channelPresentation(channel: string | null | undefined): OperationalPresentation {
  const normalized = normalizeStatus(channel)
  if (normalized === 'whatsapp') return { tone: 'default', label: 'WhatsApp' }
  if (normalized === 'webchat') return { tone: 'default', label: 'WebChat' }
  return { tone: 'default', label: String(channel ?? '').trim() || '未知渠道' }
}

export function sourceConversationPresentation(
  item: Pick<SupportConversation, 'needs_human' | 'handoff_status' | 'ai_pending' | 'status'>,
): OperationalPresentation {
  if (item.needs_human) return { tone: 'danger', label: '待人工' }
  if (item.handoff_status === 'accepted') return { tone: 'default', label: '人工处理中' }
  if (item.ai_pending) return { tone: 'warning', label: 'AI 处理中' }

  const normalized = normalizeStatus(item.status)
  if (normalized === 'resolved') return { tone: 'default', label: '来源状态：已解决' }
  if (normalized === 'closed') return { tone: 'default', label: '来源状态：已关闭' }
  if (!normalized || normalized === 'open') return { tone: 'default', label: '打开' }
  return { tone: 'default', label: `来源状态：${String(item.status).trim()}` }
}

export function controlledActionPresentation(
  status: string | null | undefined,
  message?: string | null,
): OperationalPresentation {
  const normalized = normalizeStatus(status)
  const backendDetail = String(message ?? '').trim() || null

  if (normalized === 'queued') {
    return { tone: 'default', label: '请求已排队', detail: '请求已进入处理队列；这不代表运营结果已经完成。' }
  }
  if (normalized === 'submitted') {
    return { tone: 'default', label: '请求已提交，等待确认', detail: '最终结果仍需人工或来源系统确认。' }
  }
  if (REQUEST_PENDING.has(normalized)) {
    return { tone: 'warning', label: '请求处理中', detail: '请等待后端返回可验证的运营结果。' }
  }
  if (REQUEST_FAILED.has(normalized)) {
    return { tone: 'danger', label: '请求失败或需要修复', detail: backendDetail }
  }
  if (VERIFIED_OUTCOME.has(normalized)) {
    return { tone: 'success', label: '结果已确认', detail: backendDetail }
  }
  return { tone: 'warning', label: '结果状态待确认', detail: backendDetail }
}

export function runtimePresentation(input: RuntimePresentationInput): OperationalPresentation {
  if (input.isError) return { tone: 'danger', label: '不可用' }
  if (input.isLoading) return { tone: 'warning', label: '检查中' }
  if (input.ok !== true) return { tone: 'danger', label: '未就绪' }
  if ((input.warnings?.length ?? 0) > 0) return { tone: 'warning', label: '需要关注' }
  return { tone: 'success', label: '正常' }
}
