import type { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

export type BadgeTone = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'ai'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
}

export function Badge({ tone = 'default', className, children, ...props }: BadgeProps) {
  return (
    <span className={clsx('nd-badge', `nd-badge--${tone}`, className)} {...props}>
      {children}
    </span>
  )
}
