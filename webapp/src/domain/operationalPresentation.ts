import type { BadgeTone } from '@/lib/types'

export type OperationalPresentation = {
  tone: BadgeTone
  label: string
  detail?: string
}

export type OperationalPhase =
  | 'accepted'
  | 'queued'
  | 'processing'
  | 'technical_complete'
  | 'operational_complete'
  | 'business_confirmed'
  | 'customer_notified'
  | 'failed'
  | 'repair_required'
  | 'unknown'

const STATUS_PHASES: Record<string, OperationalPhase> = {
  accepted: 'accepted',
  submitted: 'accepted',
  queued: 'queued',
  pending: 'queued',
  retryable: 'queued',
  retry_scheduled: 'queued',
  processing: 'processing',
  requested: 'processing',
  completed: 'technical_complete',
  done: 'technical_complete',
  succeeded: 'technical_complete',
  sent: 'technical_complete',
  dispatched: 'technical_complete',
  operational_completed: 'operational_complete',
  business_result_confirmed: 'business_confirmed',
  customer_notified: 'customer_notified',
  delivered: 'customer_notified',
  read: 'customer_notified',
  failed: 'failed',
  error: 'failed',
  rejected: 'failed',
  blocked: 'failed',
  dead: 'repair_required',
  dead_letter: 'repair_required',
  exhausted: 'repair_required',
  repair_required: 'repair_required',
}

export function normalizeOperationalStatus(value: unknown): string {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
}

export function operationalPhase(value: unknown): OperationalPhase {
  return STATUS_PHASES[normalizeOperationalStatus(value)] ?? 'unknown'
}

export function operationalPresentation(statusValue: unknown, messageValue?: unknown): OperationalPresentation {
  const phase = operationalPhase(statusValue)
  const detail = String(messageValue ?? '').trim() || undefined

  if (phase === 'accepted') return { tone: 'default', label: '请求已接受', detail: detail || '系统已接受请求，仍需等待最终结果。' }
  if (phase === 'queued') return { tone: 'warning', label: '请求已排队', detail: detail || '后台尚未完成执行。' }
  if (phase === 'processing') return { tone: 'warning', label: '请求处理中', detail: detail || '当前没有可验证的最终业务结果。' }
  if (phase === 'technical_complete') return { tone: 'default', label: '技术处理完成', detail: detail || '技术成功不等于运营完成、客户通知或安全结案。' }
  if (phase === 'operational_complete') return { tone: 'success', label: '运营已完成', detail: detail || '仍需按业务要求确认客户通知与结案条件。' }
  if (phase === 'business_confirmed') return { tone: 'success', label: '业务结果已确认', detail }
  if (phase === 'customer_notified') return { tone: 'success', label: '客户通知已确认', detail: detail || '通知状态不自动等于客户已读。' }
  if (phase === 'failed') return { tone: 'danger', label: '请求失败', detail: detail || '操作未形成可接受结果。' }
  if (phase === 'repair_required') return { tone: 'danger', label: '需要修复', detail: detail || '自动处理已停止，需要人工修复或重试。' }
  return { tone: 'warning', label: '结果待确认', detail: detail || '当前记录不足以判断业务结果。' }
}
