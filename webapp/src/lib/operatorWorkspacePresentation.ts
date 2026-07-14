import type { BadgeTone, SupportMemoryTimelineItem } from '@/lib/types'
import type {
  UnifiedOperatorQueueItem,
  UnifiedQueueOwner,
  UnifiedQueueRetry,
  UnifiedQueueSla,
} from '@/lib/operatorWorkspaceTypes'

export interface WorkspacePresentation {
  label: string
  detail?: string
  tone: BadgeTone
  className?: string
}

function normalized(value: unknown) {
  return String(value || '').trim().toLowerCase()
}

function compactDuration(seconds: number | null | undefined) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null
  const absolute = Math.abs(seconds)
  if (absolute < 60) return `${absolute} 秒`
  if (absolute < 3600) return `${Math.ceil(absolute / 60)} 分钟`
  if (absolute < 86400) return `${Math.ceil(absolute / 3600)} 小时`
  return `${Math.ceil(absolute / 86400)} 天`
}

export function queueSourcePresentation(source: UnifiedOperatorQueueItem['source_type']): WorkspacePresentation {
  if (source === 'handoff') return { label: '客户请求人工', detail: '客户希望由客服继续处理', tone: 'warning' }
  if (source === 'dispatch') return { label: '运营协同', detail: '需要与派送或运营团队协同', tone: 'default' }
  return { label: '客服工单', detail: '需要处理的客户案例', tone: 'default' }
}

export function priorityPresentation(priority: UnifiedOperatorQueueItem['priority']): WorkspacePresentation {
  if (priority === 'urgent') return { label: '紧急', tone: 'danger' }
  if (priority === 'high') return { label: '高优先级', tone: 'warning' }
  if (priority === 'low') return { label: '低优先级', tone: 'default' }
  return { label: '普通', tone: 'default' }
}

export function ownerPresentation(owner: UnifiedQueueOwner, currentUserId?: number): WorkspacePresentation {
  if (owner.kind === 'unassigned') return { label: '未分配', detail: '需要客服接手', tone: 'warning' }
  if (owner.kind === 'worker_lease') return { label: '系统处理中', detail: '后台任务正在执行', tone: 'default' }
  if (owner.kind === 'team') return { label: `团队 #${owner.team_id ?? '—'}`, detail: '由团队共同负责', tone: 'default' }
  if (owner.user_id && owner.user_id === currentUserId) return { label: '我负责', tone: 'default' }
  return { label: `客服 #${owner.user_id ?? '—'}`, detail: '已分配给其他客服', tone: 'default' }
}

export function slaPresentation(sla: UnifiedQueueSla): WorkspacePresentation {
  const duration = compactDuration(sla.seconds_remaining)
  if (sla.state === 'breached') return { label: '已超时', detail: duration ? `已超时 ${duration}` : '需要立即处理', tone: 'danger' }
  if (sla.state === 'at_risk') return { label: '即将超时', detail: duration ? `剩余 ${duration}` : '请优先处理', tone: 'warning' }
  if (sla.state === 'paused') return { label: '计时暂停', detail: '等待客户或运营条件满足', tone: 'default' }
  if (sla.state === 'stale') return { label: '长期未更新', detail: '需要确认案例是否仍有效', tone: 'warning' }
  if (sla.state === 'unavailable') return { label: '无时限数据', detail: '当前没有可靠的处理时限', tone: 'warning' }
  if (sla.state === 'not_applicable') return { label: '无时限要求', tone: 'default' }
  return { label: '时限正常', detail: duration ? `剩余 ${duration}` : undefined, tone: 'success' }
}

export function retryPresentation(retry: UnifiedQueueRetry): WorkspacePresentation {
  if (retry.state === 'exhausted') {
    return {
      label: '需要人工修复',
      detail: retry.error_category ? `问题类型：${retry.error_category}` : '自动重试未成功',
      tone: 'danger',
    }
  }
  if (retry.state === 'retry_scheduled') return { label: '等待重试', detail: `已尝试 ${retry.attempt_count}/${retry.max_attempts}`, tone: 'warning' }
  if (retry.state === 'processing') return { label: '执行中', detail: '后台正在处理', tone: 'default' }
  if (retry.state === 'pending') return { label: '等待执行', detail: '已进入处理队列', tone: 'default' }
  if (retry.state === 'settled') return { label: '执行已稳定', detail: '仍需确认客户问题是否解决', tone: 'default' }
  return { label: '无需重试', tone: 'default' }
}

export function sourceStatusPresentation(value: string): WorkspacePresentation {
  const status = normalized(value)
  const labels: Record<string, string> = {
    new: '新建',
    pending_assignment: '待分配',
    in_progress: '处理中',
    waiting_customer: '等待客户',
    waiting_internal: '等待运营',
    escalated: '已升级',
    resolved: '来源已解决',
    closed: '来源已关闭',
    canceled: '来源已取消',
    requested: '等待人工响应',
    accepted: '人工已接手',
    processing: '执行中',
    pending: '等待执行',
    retryable: '等待重试',
    dispatched: '已派发',
    failed: '执行失败',
    dead_letter: '需要人工修复',
  }
  const label = labels[status] || (value || '状态未知')
  const danger = ['failed', 'dead_letter'].includes(status)
  const warning = ['escalated', 'requested', 'retryable'].includes(status)
  return {
    label,
    detail: ['resolved', 'closed', 'dispatched'].includes(status) ? '该状态不代表客户问题已经完成' : undefined,
    tone: danger ? 'danger' : warning ? 'warning' : 'default',
  }
}

export type EvidenceClass =
  | 'authoritative'
  | 'claim'
  | 'knowledge'
  | 'guidance'
  | 'human'
  | 'system'
  | 'outcome'
  | 'notification'

export interface EvidencePresentation extends WorkspacePresentation {
  evidenceClass: EvidenceClass
}

export function evidencePresentation(item: SupportMemoryTimelineItem): EvidencePresentation {
  const kind = normalized(item.kind)
  const label = normalized(item.label)
  const joined = `${kind} ${label}`

  if (kind === 'outbound' || joined.includes('outbound') || joined.includes('message_sent')) {
    return { evidenceClass: 'notification', label: '客户通知记录', detail: '仅表示渠道记录，不自动证明客户已经看到', tone: 'default' }
  }
  if (kind === 'ai_turn' || joined.includes('ai turn') || joined.includes('runtime')) {
    return { evidenceClass: 'guidance', label: '历史处理建议', detail: '仅供参考，不能替代运单和运营事实', tone: 'default', className: 'is-guidance' }
  }
  if (joined.includes('knowledge') || joined.includes('policy') || joined.includes('sop')) {
    return { evidenceClass: 'knowledge', label: '知识与规则', detail: '用于指导客服处理，不替代实时运营事实', tone: 'default' }
  }
  if (joined.includes('customer') || joined.includes('visitor') || joined.includes('claim')) {
    return { evidenceClass: 'claim', label: '客户说明', detail: '需要结合权威来源核实', tone: 'warning' }
  }
  if (joined.includes('handoff') || joined.includes('human') || joined.includes('decision')) {
    return { evidenceClass: 'human', label: '客服决定', detail: '由客服或主管作出的处理决定', tone: 'default' }
  }
  if (joined.includes('work_order') || joined.includes('address_update') || joined.includes('cancel') || joined.includes('dispatch')) {
    return { evidenceClass: 'outcome', label: '处理结果', detail: '需要继续确认运营结果和客户通知', tone: 'default' }
  }
  if (
    kind === 'tool_call'
    || joined.includes('tracking_fact')
    || joined.includes('waybill')
    || joined.includes('speedaf.order.query')
  ) {
    const status = normalized(item.status)
    const verified = ['success', 'completed', 'ok'].includes(status)
    return {
      evidenceClass: 'authoritative',
      label: '已核实事实',
      detail: verified ? '来自已记录的运单或运营来源' : '事实来源当前未确认成功',
      tone: verified ? 'success' : 'warning',
    }
  }
  return { evidenceClass: 'system', label: '系统记录', detail: '系统记录不自动等于业务事实或完成', tone: 'default' }
}

export function outcomePresentation(statusValue: unknown, messageValue?: unknown): WorkspacePresentation {
  const status = normalized(statusValue)
  const message = String(messageValue || '').trim()
  if (status === 'business_result_confirmed') return { label: '客户问题已确认解决', detail: message || undefined, tone: 'success' }
  if (status === 'customer_notified' || status === 'delivered') return { label: '客户已收到通知', detail: message || undefined, tone: 'success' }
  if (status === 'operational_completed') return { label: '运营处理已完成', detail: message || undefined, tone: 'success' }
  if (['repair_required', 'failed', 'error', 'dead', 'dead_letter', 'exhausted'].includes(status)) {
    return { label: '需要人工修复', detail: message || '当前处理没有形成可接受结果', tone: 'danger' }
  }
  if (['completed', 'done', 'succeeded', 'sent', 'dispatched'].includes(status)) {
    return { label: '系统处理已完成', detail: message || '仍需确认运营结果和客户通知', tone: 'default' }
  }
  if (['queued', 'pending', 'retryable', 'retry_scheduled'].includes(status)) {
    return { label: '等待处理', detail: message || '请求已进入队列，尚未完成', tone: 'warning' }
  }
  if (['accepted', 'submitted', 'processing'].includes(status)) {
    return { label: '系统已受理', detail: message || '请求已受理，仍需等待实际结果', tone: 'default' }
  }
  if (status === 'cancel_requested') return { label: '取消请求已提交', detail: message || '仍需确认最终取消结果', tone: 'warning' }
  return { label: '结果待确认', detail: message || '当前记录不足以判断客户问题是否解决', tone: 'warning' }
}

export function messageDeliveryPresentation(statusValue: unknown): WorkspacePresentation {
  const status = normalized(statusValue)
  if (status === 'delivered' || status === 'read') return { label: status === 'read' ? '客户已读' : '已送达', tone: 'success' }
  if (status === 'sent') return { label: '已发送到渠道', detail: '不等于客户已经看到', tone: 'default' }
  if (status === 'queued' || status === 'pending') return { label: '等待发送', detail: '消息仍在发送队列中', tone: 'warning' }
  if (status === 'failed' || status === 'dead') return { label: '发送失败', detail: '需要重试或人工处理', tone: 'danger' }
  return { label: '送达状态未知', detail: '当前没有可靠的渠道回执', tone: 'warning' }
}
