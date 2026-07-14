import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { formatDateTime } from '@/lib/format'
import {
  ownerPresentation,
  priorityPresentation,
  queueSourcePresentation,
  retryPresentation,
  slaPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type { UnifiedOperatorQueueItem } from '@/lib/operatorWorkspaceTypes'

function QueueItem({
  item,
  selected,
  currentUserId,
  onSelect,
}: {
  item: UnifiedOperatorQueueItem
  selected: boolean
  currentUserId?: number
  onSelect: () => void
}) {
  const source = queueSourcePresentation(item.source_type)
  const priority = priorityPresentation(item.priority)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)

  return (
    <button
      type="button"
      className={`case-queue-item${selected ? ' is-selected' : ''}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <span className="case-queue-item__line">
        <strong>{item.case_key || `案例 ${item.source_id}`}</strong>
        <Badge tone={priority.tone}>{priority.label}</Badge>
      </span>
      <span className="case-queue-item__summary">
        <Badge tone={source.tone}>{source.label}</Badge>
        {item.reopened ? <Badge tone="warning">重新打开</Badge> : null}
        <span>{item.country_code} · {item.channel_key}</span>
      </span>
      <span className="case-queue-item__line muted">
        <span>{owner.label}</span>
        <span className={sla.tone === 'danger' ? 'is-danger' : sla.tone === 'warning' ? 'is-warning' : ''}>{sla.label}</span>
      </span>
      {item.source_type === 'dispatch' ? <span className="case-queue-item__note">{retry.label}</span> : null}
      <time>{formatDateTime(item.updated_at)}</time>
    </button>
  )
}

export function QueuePanel({
  items,
  selectedQueueId,
  currentUserId,
  loading,
  refreshing,
  hasNextPage,
  loadingMore,
  onSelect,
  onLoadMore,
}: {
  items: UnifiedOperatorQueueItem[]
  selectedQueueId: string | null
  currentUserId?: number
  loading: boolean
  refreshing: boolean
  hasNextPage: boolean
  loadingMore: boolean
  onSelect: (item: UnifiedOperatorQueueItem) => void
  onLoadMore: () => void
}) {
  return (
    <section className="case-queue" aria-labelledby="case-queue-title" aria-busy={loading || refreshing}>
      <div className="workspace-section-heading">
        <div>
          <h2 id="case-queue-title">客户待办</h2>
          <p>一个列表处理客户请求、客服工单和运营协同。</p>
        </div>
        <span className="queue-count">{items.length}</span>
      </div>

      {loading ? <EmptyState title="正在读取待办" description="正在确认你的工作范围和权限。" /> : null}
      {!loading && !items.length ? <EmptyState title="当前没有待办" description="可调整筛选条件或确认工作范围。" /> : null}
      {items.length ? (
        <div className="case-queue__items">
          {items.map((item) => (
            <QueueItem
              key={item.queue_id}
              item={item}
              selected={selectedQueueId === item.queue_id}
              currentUserId={currentUserId}
              onSelect={() => onSelect(item)}
            />
          ))}
        </div>
      ) : null}
      {hasNextPage ? (
        <Button variant="secondary" loading={loadingMore} loadingLabel="加载中…" onClick={onLoadMore}>
          加载更多待办
        </Button>
      ) : null}
      {refreshing && !loading ? <p className="queue-refreshing" role="status">正在同步最新状态…</p> : null}
    </section>
  )
}
