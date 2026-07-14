import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import type { BadgeTone } from '@/lib/types'

export function Badge({ children, tone = 'default', className }: { children: ReactNode; tone?: BadgeTone; className?: string }) {
  return <span className={cn('nd-badge', `nd-badge--${tone}`, className)}>{children}</span>
}
