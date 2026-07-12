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
  loading = false,
  loadingLabel = '处理中…',
  type = 'button',
  disabled,
  ...rest
}, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'button',
        'nd-button',
        `nd-button--${variant}`,
        `nd-button--${size}`,
        variant !== 'secondary' && variant,
        className,
      )}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <span className="nd-button__spinner" aria-hidden="true" /> : null}
      <span className="nd-button__label">{loading ? loadingLabel : children}</span>
    </button>
  )
})
