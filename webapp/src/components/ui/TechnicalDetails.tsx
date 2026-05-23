import { ReactNode } from 'react'

export function TechnicalDetails({ title = '高级技术详情', summary, children }: { title?: string; summary?: string; children: ReactNode }) {
  return (
    <details className="technical-details">
      <summary>
        <span>{title}</span>
        {summary ? <small>{summary}</small> : null}
      </summary>
      <div className="technical-details-body">{children}</div>
    </details>
  )
}
