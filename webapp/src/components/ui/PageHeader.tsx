import type { ReactNode } from 'react'

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  headingLevel = 2,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
  headingLevel?: 1 | 2 | 3
}) {
  const Heading = `h${headingLevel}` as 'h1' | 'h2' | 'h3'

  return (
    <header className="page-header">
      <div>
        {eyebrow ? <div className="page-eyebrow">{eyebrow}</div> : null}
        <Heading className="page-title">{title}</Heading>
        {description ? <p className="page-description">{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  )
}
