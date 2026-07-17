import { normalizeOperationalStatus, operationalPresentation } from '@/domain/operationalPresentation'
import type { OperationalPresentation } from '@/domain/operationalPresentation'
import type { SupportConversation } from '@/lib/types'

export type { OperationalPresentation } from '@/domain/operationalPresentation'

export type RuntimePresentationInput = {
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

export function healthPresentation(value: string | null | undefined): OperationalPresentation {
  const normalized = normalizeOperationalStatus(value)
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
  const normalized = normalizeOperationalStatus(value)
  if (normalized === 'active' || normalized === 'published') return { tone: 'success', label: '已上线' }
  if (normalized === 'draft') return { tone: 'warning', label: '草稿' }
  if (normalized === 'archived') return { tone: 'default', label: '已归档' }
  return { tone: 'warning', label: String(value ?? '').trim() || '状态未知' }
}

export function channelPresentation(channel: string | null | undefined): OperationalPresentation {
  const normalized = normalizeOperationalStatus(channel)
  const labels: Record<string, string> = {
    webchat: '网页客服',
    web_chat: '网页客服',
    whatsapp: 'WhatsApp',
    email: '邮件',
    voice: '语音',
    sms: '短信',
    telegram: 'Telegram',
  }
  return {
    tone: 'default',
    label: labels[normalized] || String(channel ?? '').trim() || '未知渠道',
  }
}

export function sourceConversationPresentation(
  item: Pick<SupportConversation, 'needs_human' | 'handoff_status' | 'ai_pending' | 'status'>,
): OperationalPresentation {
  if (item.needs_human) return { tone: 'danger', label: '待人工' }
  if (item.handoff_status === 'accepted') return { tone: 'default', label: '人工处理中' }
  if (item.ai_pending) return { tone: 'warning', label: 'AI 处理中' }

  const normalized = normalizeOperationalStatus(item.status)
  if (normalized === 'resolved') return { tone: 'default', label: '来源状态：已解决' }
  if (normalized === 'closed') return { tone: 'default', label: '来源状态：已关闭' }
  if (!normalized || normalized === 'open') return { tone: 'default', label: '打开' }
  return { tone: 'default', label: `来源状态：${String(item.status).trim()}` }
}

export function controlledActionPresentation(
  status: string | null | undefined,
  message?: string | null,
): OperationalPresentation {
  return operationalPresentation(status, message)
}

export function runtimePresentation(input: RuntimePresentationInput): OperationalPresentation {
  if (input.isError) return { tone: 'danger', label: '不可用' }
  if (input.isLoading) return { tone: 'warning', label: '检查中' }
  if (input.ok !== true) return { tone: 'danger', label: '未就绪' }
  if ((input.warnings?.length ?? 0) > 0) return { tone: 'warning', label: '需要关注' }
  return { tone: 'success', label: '正常' }
}
