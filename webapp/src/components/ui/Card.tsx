import type { HTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

export function Card({ className, children, ...props }: PropsWithChildren<HTMLAttributes<HTMLElement>>) {
  return <section {...props} className={cn('nd-card', className)}>{children}</section>
}

export function CardHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return <div className="nd-card__header"><div><h3 className="nd-card__title">{title}</h3>{subtitle ? <p className="nd-card__subtitle">{subtitle}</p> : null}</div></div>
}

export function CardBody({ children }: PropsWithChildren) {
  return <div className="nd-card__body">{children}</div>
}
