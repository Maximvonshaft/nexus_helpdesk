import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { clsx } from 'clsx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  leadingIcon,
  trailingIcon,
  className,
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx('nd-button', `nd-button--${variant}`, `nd-button--${size}`, className)}
      {...props}
    >
      {leadingIcon ? <span className="nd-button__icon" aria-hidden="true">{leadingIcon}</span> : null}
      <span className="nd-button__label">{children}</span>
      {trailingIcon ? <span className="nd-button__icon" aria-hidden="true">{trailingIcon}</span> : null}
    </button>
  )
}
