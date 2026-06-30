import { forwardRef } from 'react'
import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

type ButtonProps = PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & { variant?: Variant }

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ children, className, variant = 'secondary', type = 'button', ...rest }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn('button', variant !== 'secondary' && variant, className)}
      {...rest}
    >
      {children}
    </button>
  )
})
