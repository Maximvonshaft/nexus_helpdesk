import type { HTMLAttributes, ReactNode } from 'react'
import { clsx } from 'clsx'

export interface CardProps extends HTMLAttributes<HTMLElement> {
  elevated?: boolean
}

export function Card({ elevated = false, className, children, ...props }: CardProps) {
  return (
    <section className={clsx('nd-card', elevated && 'nd-card--elevated', className)} {...props}>
      {children}
    </section>
  )
}

export interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}

export function CardHeader({ title, subtitle, actions, className, ...props }: CardHeaderProps) {
  return (
    <div className={clsx('nd-card__header', className)} {...props}>
      <div className="nd-card__heading">
        <h2 className="nd-card__title">{title}</h2>
        {subtitle ? <p className="nd-card__subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="nd-card__actions">{actions}</div> : null}
    </div>
  )
}

export function CardBody({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx('nd-card__body', className)} {...props}>
      {children}
    </div>
  )
}
