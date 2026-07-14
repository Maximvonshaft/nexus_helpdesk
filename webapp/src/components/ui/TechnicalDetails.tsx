import type { ReactNode } from 'react'

export function TechnicalDetails({ title = '详细信息', summary, children }: { title?: string; summary?: string; children: ReactNode }) {
  return (
    <details className="nd-details">
      <summary><span>{title}</span>{summary ? <small>{summary}</small> : null}</summary>
      <div className="nd-details__body">{children}</div>
    </details>
  )
}
