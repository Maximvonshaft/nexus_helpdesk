import { forwardRef, type ButtonHTMLAttributes, type PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

export const Button = forwardRef<HTMLButtonElement, PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & { variant?: Variant }>(
  function Button({ children, className, ...props }, ref) {
    const variant = props.variant ?? 'secondary'
    const { variant: _ignoredVariant, ...rest } = props as any
    return (
      <button
        ref={ref}
        className={cn('button', variant !== 'secondary' && variant, className)}
        {...rest}
      >
        {children}
      </button>
    )
  },
)
