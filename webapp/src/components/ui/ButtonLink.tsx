import type { AnchorHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import type { ButtonSize, ButtonVariant } from './Button'

export type ButtonLinkProps = PropsWithChildren<AnchorHTMLAttributes<HTMLAnchorElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

export function ButtonLink({
  children,
  className,
  variant = 'secondary',
  size = 'md',
  leadingIcon,
  trailingIcon,
  ...props
}: ButtonLinkProps) {
  return (
    <a
      className={cn('nd-button', `nd-button--${size}`, `nd-button--${variant}`, className)}
      {...props}
    >
      {leadingIcon ? <span className="nd-button__icon" aria-hidden="true">{leadingIcon}</span> : null}
      <span className="nd-button__label">{children}</span>
      {trailingIcon ? <span className="nd-button__icon" aria-hidden="true">{trailingIcon}</span> : null}
    </a>
  )
}
