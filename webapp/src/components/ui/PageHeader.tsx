import type { ElementType, ReactNode } from 'react'

type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

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
  headingLevel?: HeadingLevel
}) {
  const Heading = `h${headingLevel}` as ElementType

  return (
    <div className="page-header nd-page-header">
      <div>
        {eyebrow ? <div className="page-eyebrow nd-page-header__eyebrow">{eyebrow}</div> : null}
        <Heading className="page-title nd-page-header__title">{title}</Heading>
        {description ? <p className="page-description nd-page-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="page-actions nd-page-header__actions">{actions}</div> : null}
    </div>
  )
}
