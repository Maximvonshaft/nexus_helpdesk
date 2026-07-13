import { cn } from '@/lib/cn'
import type { BadgeTone } from '@/lib/types'

export function Badge({ children, tone = 'default' }: { children: React.ReactNode; tone?: BadgeTone }) {
  return (
    <span className={cn('badge', 'nd-badge', `nd-badge--${tone}`, tone !== 'default' && tone)}>
      {children}
    </span>
  )
}
