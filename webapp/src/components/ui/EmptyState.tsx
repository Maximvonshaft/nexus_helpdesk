import { ReactNode } from 'react'

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
  if (!title && !description && !reason && !action) return <div className="empty">{text}</div>
  return (
    <div className="empty-state" role="status">
      <strong>{title ?? text}</strong>
      {description ? <p>{description}</p> : null}
      {reason ? <small>{reason}</small> : null}
      {action ? <div className="empty-state-action">{action}</div> : null}
    </div>
  )
}
