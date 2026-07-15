import { forwardRef } from 'react'
import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export type ButtonProps = PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  loadingLabel?: string
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  children,
  className,
  variant = 'secondary',
  size = 'md',
  type = 'button',
  loading = false,
  loadingLabel = '处理中…',
  leadingIcon,
  trailingIcon,
  disabled,
  ...rest
}, ref) {
  const label = loading ? loadingLabel : children

  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'nd-button',
        `nd-button--${size}`,
        `nd-button--${variant}`,
        className,
      )}
      aria-busy={loading ? true : undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {!loading && leadingIcon ? <span className="nd-button__icon" aria-hidden="true">{leadingIcon}</span> : null}
      <span className="nd-button__label">{label}</span>
      {!loading && trailingIcon ? <span className="nd-button__icon" aria-hidden="true">{trailingIcon}</span> : null}
    </button>
  )
})
