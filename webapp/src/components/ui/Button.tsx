import { forwardRef } from 'react'
import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

type ButtonProps = PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & {
  variant?: Variant
  size?: Size
  loading?: boolean
  loadingLabel?: string
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  children,
  className,
  variant = 'secondary',
  size = 'md',
  type = 'button',
  loading = false,
  loadingLabel = '处理中…',
  disabled,
  ...rest
}, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn('nd-button', `nd-button--${size}`, `nd-button--${variant}`, className)}
      aria-busy={loading ? true : undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? loadingLabel : children}
    </button>
  )
})
