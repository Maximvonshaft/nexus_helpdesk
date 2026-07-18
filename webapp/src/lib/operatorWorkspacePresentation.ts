import {
  normalizeOperationalStatus,
  operationalPresentation,
} from '@/domain/operationalPresentation'
import type { OperationalPresentation } from '@/domain/operationalPresentation'
import type {
  SupportMemoryTimelineItem,
  WebchatMessage,
} from '@/lib/types'
import type {
  UnifiedOperatorQueueItem,
  UnifiedQueueOwner,
  UnifiedQueueRetry,
  UnifiedQueueSla,
} from '@/lib/operatorWorkspaceTypes'

function compactDuration(seconds: number | null | undefined) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null
  const absolute = Math.abs(seconds)
  if (absolute < 60) return `${absolute} 秒`
  if (absolute < 3600) return `${Math.ceil(absolute / 60)} 分钟`
  if (absolute < 86400) return `${Math.ceil(absolute / 3600)} 小时`
  return `${Math.ceil(absolute / 86400)} 天`
}

export function queueSourcePresentation(source: UnifiedOperatorQueueItem['source_type']): OperationalPresentation {
  if (source === 'handoff') return { label: '待接手', tone: 'warning' }
  if (source === 'dispatch') return { label: '内部任务', tone: 'default' }
  return { label: '客服工单', tone: 'default' }
}

export function priorityPresentation(priority: UnifiedOperatorQueueItem['priority']): OperationalPresentation {
  if (priority === 'urgent') return { label: '紧急', tone: 'danger' }
  if (priority === 'high') return { label: '高优先级', tone: 'warning' }
  if (priority === 'low') return { label: '低优先级', tone: 'default' }
  return { label: '普通', tone: 'default' }
}

export function ownerPresentation(owner: UnifiedQueueOwner, currentUserId?: number): OperationalPresentation {
  if (owner.kind === 'unassigned') return { label: '未分配', tone: 'warning' }
  if (owner.kind === 'worker_lease') return { label: '自动处理中', tone: 'default' }
  if (owner.kind === 'team') return { label: `团队 #${owner.team_id ?? '—'}`, tone: 'default' }
  if (owner.user_id && owner.user_id === currentUserId) return { label: '我负责', tone: 'default' }
  return { label: `客服 #${owner.user_id ?? '—'}`, tone: 'default' }
}

export function slaPresentation(sla: UnifiedQueueSla): OperationalPresentation {
  const duration = compactDuration(sla.seconds_remaining)
  if (sla.state === 'breached') return { label: '已超时', detail: duration ? `超时 ${duration}` : undefined, tone: 'danger' }
  if (sla.state === 'at_risk') return { label: '即将超时', detail: duration ? `剩余 ${duration}` : undefined, tone: 'warning' }
  if (sla.state === 'paused') return { label: '计时已暂停', tone: 'default' }
  if (sla.state === 'stale') return { label: '长期未更新', tone: 'warning' }
  if (sla.state === 'unavailable') return { label: '时限未知', tone: 'warning' }
  if (sla.state === 'not_applicable') return { label: '无处理时限', tone: 'default' }
  return { label: duration ? `剩余 ${duration}` : '时限正常', tone: 'success' }
}

export function retryPresentation(retry: UnifiedQueueRetry): OperationalPresentation {
  if (retry.state === 'exhausted') {
    return {
      label: '自动重试失败',
      detail: retry.error_category ? `错误：${retry.error_category}` : undefined,
      tone: 'danger',
    }
  }
  if (retry.state === 'retry_scheduled') {
    return {
      label: '等待重试',
      detail: `${retry.attempt_count}/${retry.max_attempts} 次`,
      tone: 'warning',
    }
  }
  if (retry.state === 'processing') return { label: '正在执行', tone: 'default' }
  if (retry.state === 'pending') return { label: '等待执行', tone: 'default' }
  if (retry.state === 'settled') return { label: '执行已结束', tone: 'default' }
  return { label: '无需重试', tone: 'default' }
}

export function sourceStatusPresentation(value: string): OperationalPresentation {
  const status = normalizeOperationalStatus(value)
  const labels: Record<string, string> = {
    new: '新建',
    pending_assignment: '待分配',
    in_progress: '处理中',
    waiting_customer: '等待客户',
    waiting_internal: '等待内部处理',
    escalated: '已升级',
    resolved: '来源已解决',
    closed: '来源已关闭',
    canceled: '来源已取消',
    requested: '等待接手',
    accepted: '已接手',
    processing: '处理中',
    pending: '等待执行',
    retryable: '等待重试',
    dispatched: '已派发',
    failed: '执行失败',
    dead_letter: '无法自动处理',
  }
  const label = labels[status] || sanitizeSourceStatus(value)
  const danger = ['failed', 'dead_letter'].includes(status)
  const warning = ['escalated', 'requested', 'retryable'].includes(status)
  return { label, tone: danger ? 'danger' : warning ? 'warning' : 'default' }
}

function sanitizeSourceStatus(value: string) {
  const compact = String(value || '').trim()
  return compact || '状态未知'
}

export type EvidenceClass =
  | 'authoritative'
  | 'claim'
  | 'knowledge'
  | 'ai'
  | 'human'
  | 'system'
  | 'outcome'
  | 'notification'

export interface EvidencePresentation extends OperationalPresentation {
  evidenceClass: EvidenceClass
}

export function evidencePresentation(item: SupportMemoryTimelineItem): EvidencePresentation {
  const kind = normalizeOperationalStatus(item.kind)
  const label = normalizeOperationalStatus(item.label)
  const joined = `${kind} ${label}`

  if (kind === 'outbound' || joined.includes('outbound') || joined.includes('message_sent')) {
    return { evidenceClass: 'notification', label: '客户通知', tone: 'default' }
  }
  if (kind === 'ai_turn' || joined.includes('ai_turn') || joined.includes('runtime')) {
    return { evidenceClass: 'ai', label: '自动回复建议', tone: 'default' }
  }
  if (joined.includes('knowledge') || joined.includes('policy') || joined.includes('sop')) {
    return { evidenceClass: 'knowledge', label: '知识与政策', tone: 'default' }
  }
  if (joined.includes('customer') || joined.includes('visitor') || joined.includes('claim')) {
    return { evidenceClass: 'claim', label: '客户说法', tone: 'warning' }
  }
  if (joined.includes('handoff') || joined.includes('human') || joined.includes('decision')) {
    return { evidenceClass: 'human', label: '处理决定', tone: 'default' }
  }
  if (joined.includes('work_order') || joined.includes('address_update') || joined.includes('cancel') || joined.includes('dispatch')) {
    return { evidenceClass: 'outcome', label: '操作结果', tone: 'default' }
  }
  if (
    kind === 'tool_call'
    || joined.includes('tracking_fact')
    || joined.includes('waybill')
    || joined.includes('speedaf.order.query')
  ) {
    const status = normalizeOperationalStatus(item.status)
    const verified = ['success', 'completed', 'ok'].includes(status)
    return {
      evidenceClass: 'authoritative',
      label: verified ? '已核实信息' : '待核实信息',
      tone: verified ? 'success' : 'warning',
    }
  }
  return { evidenceClass: 'system', label: '系统记录', tone: 'default' }
}

export function outcomePresentation(statusValue: unknown, messageValue?: unknown): OperationalPresentation {
  return operationalPresentation(statusValue, messageValue)
}

export function messageDeliveryPresentation(statusValue: unknown): OperationalPresentation {
  const status = normalizeOperationalStatus(statusValue)
  if (status === 'read') return { label: '客户已读', tone: 'success' }
  if (status === 'delivered') return { label: '已送达', tone: 'success' }
  if (status === 'sent') return { label: '已发送', tone: 'default' }
  if (status === 'queued' || status === 'pending') return { label: '等待发送', tone: 'warning' }
  if (status === 'failed' || status === 'dead') return { label: '发送失败', tone: 'danger' }
  return { label: '送达状态未知', tone: 'warning' }
}

export function workspaceDirectionLabel(direction: string) {
  if (direction === 'visitor' || direction === 'customer') return '客户'
  if (direction === 'agent' || direction === 'human') return '客服'
  if (direction === 'ai') return '自动回复'
  return '系统'
}

export function isOutboundWorkspaceMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}
