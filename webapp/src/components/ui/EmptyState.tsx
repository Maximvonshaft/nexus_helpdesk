import type { ReactNode } from 'react'

export function EmptyState({
  text,
  title,
  description,
  reason,
  action,
}: {
  text?: string
  title?: string
  description?: string
  reason?: string
  action?: ReactNode
}) {
  return (
    <div className="nd-empty-state" role="status">
      <strong>{title ?? text}</strong>
      {description ? <p>{description}</p> : null}
      {reason ? <small>{reason}</small> : null}
      {action ? <div className="nd-empty-state__action">{action}</div> : null}
    </div>
  )
}
