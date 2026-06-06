import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

type ButtonProps = PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & { variant?: Variant }

export function Button({ children, className, variant = 'secondary', type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn('button', variant !== 'secondary' && variant, className)}
      {...rest}
    >
      {children}
    </button>
  )
}
