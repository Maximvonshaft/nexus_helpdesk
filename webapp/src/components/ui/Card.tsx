import { PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

export function Card({ className, children }: PropsWithChildren<{ className?: string }>) {
  return <section className={cn('card', className)}>{children}</section>
}
export function CardHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return <div className="card-header"><h3 className="section-title">{title}</h3>{subtitle ? <p className="section-subtitle">{subtitle}</p> : null}</div>
}
export function CardBody({ children }: PropsWithChildren) {
  return <div className="card-body">{children}</div>
}
