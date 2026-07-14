import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import {
  evidencePresentation,
  ownerPresentation,
  priorityPresentation,
  queueSourcePresentation,
  slaPresentation,
  sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type { UnifiedOperatorQueueItem, WorkspaceSourceRecord } from '@/lib/operatorWorkspaceTypes'
import type { SupportMemoryLedger, WebchatThread } from '@/lib/types'
import { latestCustomerMessage, textValue } from '../workspaceUtils'

function timelineHas(memory: SupportMemoryLedger | null, values: string[]) {
  const normalizedValues = values.map((value) => value.toLowerCase())
  return (memory?.evidence_timeline ?? []).some((item) => {
    const haystack = `${item.status || ''} ${item.label || ''} ${JSON.stringify(item.summary || {})}`.toLowerCase()
    return normalizedValues.some((value) => haystack.includes(value))
  })
}

function CaseProgress({ memory, thread }: { memory: SupportMemoryLedger | null; thread: WebchatThread | null }) {
  const hasCustomerRequest = Boolean(memory?.customer_request || latestCustomerMessage(thread))
  const hasFacts = Boolean(memory?.evidence_timeline?.length)
  const hasNextAction = Boolean(memory?.required_action || memory?.next_actions?.length)
  const actionStarted = timelineHas(memory, ['queued', 'submitted', 'accepted', 'processing', 'completed', 'failed'])
  const operationalComplete = timelineHas(memory, ['operational_completed'])
  const customerNotified = timelineHas(memory, ['customer_notified', 'delivered'])
  const businessConfirmed = timelineHas(memory, ['business_result_confirmed'])
  const steps = [
    ['客户诉求', hasCustomerRequest],
    ['事实核实', hasFacts],
    ['下一步', hasNextAction],
    ['执行处理', actionStarted],
    ['运营结果', operationalComplete],
    ['通知客户', customerNotified],
    ['完成案例', businessConfirmed],
  ] as const

  return (
    <section className="case-progress" aria-labelledby="case-progress-title">
      <div className="workspace-section-heading compact">
        <div>
          <h2 id="case-progress-title">处理进度</h2>
          <p>完成前必须确认客户诉求、处理结果和客户通知。</p>
        </div>
        <Badge tone={businessConfirmed ? 'success' : 'warning'}>{businessConfirmed ? '可以完成' : '尚未完成'}</Badge>
      </div>
      <ol>
        {steps.map(([label, complete], index) => (
          <li key={label} className={complete ? 'is-complete' : index === steps.findIndex(([, done]) => !done) ? 'is-current' : ''}>
            <span>{complete ? '✓' : index + 1}</span>
            <strong>{label}</strong>
          </li>
        ))}
      </ol>
      {!businessConfirmed ? (
        <div className="case-blocker" role="status">
          <strong>当前不能直接完成案例</strong>
          <p>请先确认运营处理结果，并确保客户已经收到明确回复。</p>
        </div>
      ) : null}
    </section>
  )
}

function EvidenceList({ memory, thread }: { memory: SupportMemoryLedger | null; thread: WebchatThread | null }) {
  const latestMessage = latestCustomerMessage(thread)
  const evidence = memory?.evidence_timeline ?? []
  return (
    <section className="case-evidence" aria-labelledby="case-evidence-title">
      <div className="workspace-section-heading compact">
        <div>
          <h2 id="case-evidence-title">事实与待确认信息</h2>
          <p>客户说法、系统记录和已核实事实必须分开查看。</p>
        </div>
        <span className="queue-count">{evidence.length}</span>
      </div>

      {memory?.missing_fields?.length ? (
        <div className="case-missing-fields">
          <strong>仍需补充</strong>
          <ul>{memory.missing_fields.map((field) => <li key={field}>{sanitizeDisplayText(field)}</li>)}</ul>
        </div>
      ) : null}

      {latestMessage ? (
        <article className="evidence-item is-claim">
          <div><Badge tone="warning">客户最新说明</Badge>{latestMessage.created_at ? <time>{formatDateTime(latestMessage.created_at)}</time> : null}</div>
          <p>{sanitizeDisplayText(latestMessage.body_text || latestMessage.body)}</p>
          <small>客户说明需要结合运单和运营记录核实。</small>
        </article>
      ) : null}

      {evidence.slice(0, 12).map((item) => {
        const presentation = evidencePresentation(item)
        return (
          <article className={`evidence-item ${presentation.className || ''}`} key={item.source_id || `${item.kind}-${item.created_at}-${item.label}`}>
            <div>
              <Badge tone={presentation.tone}>{presentation.label}</Badge>
              {item.created_at ? <time>{formatDateTime(item.created_at)}</time> : null}
            </div>
            <strong>{sanitizeDisplayText(item.label || item.kind || '处理记录')}</strong>
            <p>{presentation.detail}</p>
          </article>
        )
      })}

      {!latestMessage && !evidence.length ? (
        <EmptyState title="尚无可用事实" description="先查询运单或向客户补充必要信息，再决定处理方案。" />
      ) : null}
    </section>
  )
}

export function CaseOverview({
  item,
  currentUserId,
  thread,
  sourceRecord,
}: {
  item: UnifiedOperatorQueueItem
  currentUserId?: number
  thread: WebchatThread | null
  sourceRecord: WorkspaceSourceRecord | null
}) {
  const memory = thread?.support_memory ?? null
  const source = queueSourcePresentation(item.source_type)
  const priority = priorityPresentation(item.priority)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const sourceStatus = sourceStatusPresentation(item.source_status)
  const customerName = thread?.visitor?.name || thread?.visitor?.email || thread?.visitor?.phone || '客户信息待补充'
  const title = textValue(sourceRecord?.title) || thread?.ticket_no || item.case_key || `案例 ${item.source_id}`
  const nextAction = memory?.required_action || memory?.next_actions?.[0]?.label || textValue(sourceRecord?.required_action) || '核实事实并确定下一步'

  return (
    <div className="case-overview">
      <header className="case-overview__header">
        <div>
          <p>{source.label} · {item.country_code} · {item.channel_key}</p>
          <h2>{sanitizeDisplayText(title)}</h2>
          <span>{sanitizeDisplayText(customerName)}</span>
        </div>
        <div className="case-overview__badges">
          <Badge tone={priority.tone}>{priority.label}</Badge>
          <Badge tone={owner.tone}>{owner.label}</Badge>
          <Badge tone={sla.tone}>{sla.label}</Badge>
          <Badge tone={sourceStatus.tone}>{sourceStatus.label}</Badge>
        </div>
      </header>

      <section className="next-action-card" aria-labelledby="next-action-title">
        <span>当前最重要的下一步</span>
        <strong id="next-action-title">{sanitizeDisplayText(nextAction)}</strong>
        <p>一次只完成一个主要动作；不可执行时先补齐缺失信息或升级给主管。</p>
      </section>

      {sourceRecord && !thread ? (
        <section className="case-source-summary">
          <h2>案例摘要</h2>
          <dl>
            <div><dt>标题</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.title) || '未提供')}</dd></div>
            <div><dt>状态</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.status) || item.source_status)}</dd></div>
            <div><dt>优先级</dt><dd>{sanitizeDisplayText(textValue(sourceRecord.priority) || item.priority)}</dd></div>
          </dl>
        </section>
      ) : null}

      <CaseProgress memory={memory} thread={thread} />
      <EvidenceList memory={memory} thread={thread} />
    </div>
  )
}
