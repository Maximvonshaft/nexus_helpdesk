import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({ children, className, ...props }: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & { variant?: Variant }) {
  const variant = props.variant ?? 'secondary'
  const { variant: _ignoredVariant, ...rest } = props as any
  return (
    <button
      className={cn('button', variant !== 'secondary' && variant, className)}
      {...rest}
    >
      {children}
    </button>
  )
}
