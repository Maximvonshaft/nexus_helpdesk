import { operationalPresentation } from '@/domain/operationalPresentation'
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
  if (source === 'handoff') return { label: '人工接管', detail: '客户会话请求人工处理', tone: 'warning' }
  if (source === 'dispatch') return { label: '运营派发', detail: '运营路由或派发工作', tone: 'default' }
  return { label: '客服工单', detail: '需要处理的案例来源记录', tone: 'default' }
}

export function priorityPresentation(priority: UnifiedOperatorQueueItem['priority']): WorkspacePresentation {
  if (priority === 'urgent') return { label: '紧急', tone: 'danger' }
  if (priority === 'high') return { label: '高优先级', tone: 'warning' }
  if (priority === 'low') return { label: '低优先级', tone: 'default' }
  return { label: '普通', tone: 'default' }
}

export function ownerPresentation(owner: UnifiedQueueOwner, currentUserId?: number): WorkspacePresentation {
  if (owner.kind === 'unassigned') return { label: '未分配', detail: '需要有人接手', tone: 'warning' }
  if (owner.kind === 'worker_lease') return { label: '系统处理中', detail: '后台 Worker 正在处理', tone: 'default' }
  if (owner.kind === 'team') return { label: `团队 #${owner.team_id ?? '—'}`, detail: '由当前团队共同负责', tone: 'default' }
  if (owner.user_id && owner.user_id === currentUserId) return { label: '我负责', tone: 'default' }
  return { label: `客服 #${owner.user_id ?? '—'}`, detail: '已分配给其他客服', tone: 'default' }
}

export function slaPresentation(sla: UnifiedQueueSla): WorkspacePresentation {
  const duration = compactDuration(sla.seconds_remaining)
  if (sla.state === 'breached') return { label: 'SLA 已超时', detail: duration ? `已超时 ${duration}` : '需要立即处理', tone: 'danger' }
  if (sla.state === 'at_risk') return { label: 'SLA 即将超时', detail: duration ? `剩余 ${duration}` : '优先处理', tone: 'warning' }
  if (sla.state === 'paused') return { label: 'SLA 已暂停', detail: '等待条件满足后继续计时', tone: 'default' }
  if (sla.state === 'stale') return { label: '长期未更新', detail: '需要确认案例是否仍有效', tone: 'warning' }
  if (sla.state === 'unavailable') return { label: 'SLA 不可用', detail: '当前没有可信的时限数据', tone: 'warning' }
  if (sla.state === 'not_applicable') return { label: '无需 SLA', tone: 'default' }
  return { label: 'SLA 正常', detail: duration ? `剩余 ${duration}` : undefined, tone: 'success' }
}

export function retryPresentation(retry: UnifiedQueueRetry): WorkspacePresentation {
  if (retry.state === 'exhausted') {
    return {
      label: '重试已耗尽',
      detail: retry.error_category ? `错误类型：${retry.error_category}` : '需要人工修复',
      tone: 'danger',
    }
  }
  if (retry.state === 'retry_scheduled') {
    return {
      label: '等待重试',
      detail: `已尝试 ${retry.attempt_count}/${retry.max_attempts}`,
      tone: 'warning',
    }
  }
  if (retry.state === 'processing') return { label: '正在执行', detail: '后台正在处理该派发', tone: 'default' }
  if (retry.state === 'pending') return { label: '等待执行', detail: '已进入后台队列', tone: 'default' }
  if (retry.state === 'settled') return { label: '派发已稳定', detail: '技术派发状态已结束，不等于业务结案', tone: 'default' }
  return { label: '无需重试', tone: 'default' }
}

export function sourceStatusPresentation(value: string): WorkspacePresentation {
  const status = normalized(value)
  const labels: Record<string, string> = {
    new: '来源状态：新建',
    pending_assignment: '来源状态：待分配',
    in_progress: '来源状态：处理中',
    waiting_customer: '来源状态：等待客户',
    waiting_internal: '来源状态：等待运营',
    escalated: '来源状态：已升级',
    resolved: '来源状态：已解决',
    closed: '来源状态：已关闭',
    canceled: '来源状态：已取消',
    requested: '接管状态：待响应',
    accepted: '接管状态：已接受',
    processing: '派发状态：处理中',
    pending: '派发状态：等待执行',
    retryable: '派发状态：等待重试',
    dispatched: '派发状态：已派发',
    failed: '派发状态：失败',
    dead_letter: '派发状态：死信',
  }
  const label = labels[status] || `来源状态：${value || '未知'}`
  const danger = ['failed', 'dead_letter'].includes(status)
  const warning = ['escalated', 'requested', 'retryable'].includes(status)
  return {
    label,
    detail: ['resolved', 'closed', 'dispatched'].includes(status) ? '该状态不代表业务已经安全结案' : undefined,
    tone: danger ? 'danger' : warning ? 'warning' : 'default',
  }
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

export interface EvidencePresentation extends WorkspacePresentation {
  evidenceClass: EvidenceClass
}

export function evidencePresentation(item: SupportMemoryTimelineItem): EvidencePresentation {
  const kind = normalized(item.kind)
  const label = normalized(item.label)
  const joined = `${kind} ${label}`

  if (kind === 'outbound' || joined.includes('outbound') || joined.includes('message_sent')) {
    return { evidenceClass: 'notification', label: '客户通知回执', detail: '仅表示当前渠道记录，不自动证明客户已收到', tone: 'default' }
  }
  if (kind === 'ai_turn' || joined.includes('ai turn') || joined.includes('runtime')) {
    return { evidenceClass: 'ai', label: 'AI 建议', detail: 'AI 输出不是权威事实', tone: 'default', className: 'is-ai' }
  }
  if (joined.includes('knowledge') || joined.includes('policy') || joined.includes('sop')) {
    return { evidenceClass: 'knowledge', label: '知识与政策', detail: '用于指导处理，不替代实时运营事实', tone: 'default' }
  }
  if (joined.includes('customer') || joined.includes('visitor') || joined.includes('claim')) {
    return { evidenceClass: 'claim', label: '客户主张', detail: '需要通过权威来源核实', tone: 'warning' }
  }
  if (joined.includes('handoff') || joined.includes('human') || joined.includes('decision')) {
    return { evidenceClass: 'human', label: '人工决定', detail: '由操作员或接管流程产生', tone: 'default' }
  }
  if (joined.includes('work_order') || joined.includes('address_update') || joined.includes('cancel') || joined.includes('dispatch')) {
    return { evidenceClass: 'outcome', label: '动作结果', detail: '需区分请求、技术处理和业务结果', tone: 'default' }
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
      label: '事实与依据',
      detail: verified ? '来自已记录的运营工具或事实源' : '事实源当前未确认成功',
      tone: verified ? 'success' : 'warning',
    }
  }
  return { evidenceClass: 'system', label: '系统事件', detail: '系统记录，不自动等于业务事实或完成', tone: 'default' }
}

export function outcomePresentation(statusValue: unknown, messageValue?: unknown): WorkspacePresentation {
  return operationalPresentation(statusValue, messageValue)
}

export function messageDeliveryPresentation(statusValue: unknown): WorkspacePresentation {
  const status = normalized(statusValue)
  if (status === 'read') return { label: '客户已读', tone: 'success' }
  if (status === 'delivered') return { label: '已送达', detail: '送达不自动等于客户已读', tone: 'success' }
  if (status === 'sent') return { label: '已发送到渠道', detail: '不等于客户已经收到', tone: 'default' }
  if (status === 'queued' || status === 'pending') return { label: '等待发送', detail: '消息仍在发送队列中', tone: 'warning' }
  if (status === 'failed' || status === 'dead') return { label: '发送失败', detail: '需要重试或人工处理', tone: 'danger' }
  return { label: '送达状态未知', detail: '当前没有可靠的渠道回执', tone: 'warning' }
}
