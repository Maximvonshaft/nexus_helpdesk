import { EmptyState } from '@/components/ui/EmptyState'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { outcomePresentation } from '@/lib/operatorWorkspacePresentation'
import type { SupportMemoryLedger } from '@/lib/types'

export function OutcomePanel({ memory }: { memory: SupportMemoryLedger | null }) {
  const items = (memory?.evidence_timeline ?? []).filter((item) => {
    const haystack = `${item.kind || ''} ${item.label || ''} ${item.status || ''}`.toLowerCase()
    return ['outbound', 'work_order', 'address_update', 'cancel', 'dispatch', 'action'].some((marker) => haystack.includes(marker))
  })

  return (
    <section className="outcome-panel" aria-labelledby="outcome-panel-title">
      <div className="workspace-section-heading">
        <div>
          <h2 id="outcome-panel-title">处理结果</h2>
          <p>提交成功不等于客户问题已经解决；这里持续记录实际结果。</p>
        </div>
      </div>

      {items.length ? (
        <ol className="outcome-list">
          {items.slice(0, 12).map((item) => {
            const presentation = outcomePresentation(item.status, item.label)
            return (
              <li key={item.source_id || `${item.kind}-${item.created_at}-${item.label}`}>
                <span className={`outcome-dot is-${presentation.tone}`} aria-hidden="true" />
                <div>
                  <strong>{presentation.label}</strong>
                  <p>{sanitizeDisplayText(presentation.detail || '当前记录不足以判断客户问题是否已经解决。')}</p>
                  {item.created_at ? <time>{formatDateTime(item.created_at)}</time> : null}
                </div>
              </li>
            )
          })}
        </ol>
      ) : <EmptyState title="还没有处理结果" description="执行动作后，这里会显示受理、运营完成、客户通知和最终结果。" />}
    </section>
  )
}
