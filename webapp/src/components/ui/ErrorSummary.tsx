import type { ReactNode } from 'react'

export function ErrorSummary({ title = '请先处理以下问题', errors, action }: { title?: string; errors: string[]; action?: ReactNode }) {
  if (!errors.length) return null
  return (
    <div className="nd-error-summary" role="alert" aria-live="assertive">
      <strong>{title}</strong>
      <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>
      {action ? <div className="nd-error-summary__action">{action}</div> : null}
    </div>
  )
}
