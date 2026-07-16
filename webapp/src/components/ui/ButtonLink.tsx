import { Button as MuiButton } from '@mui/material'
import type { AnchorHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import type { ButtonSize, ButtonVariant } from './Button'

export type ButtonLinkProps = PropsWithChildren<AnchorHTMLAttributes<HTMLAnchorElement>> & {
  variant?: ButtonVariant
  size?: ButtonSize
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

const variantProps = {
  primary: { variant: 'contained', color: 'primary' },
  secondary: { variant: 'outlined', color: 'inherit' },
  ghost: { variant: 'text', color: 'inherit' },
  danger: { variant: 'contained', color: 'error' },
} as const

const sizeMap = {
  sm: 'small',
  md: 'medium',
  lg: 'large',
} as const

export function ButtonLink({
  children,
  variant = 'secondary',
  size = 'md',
  leadingIcon,
  trailingIcon,
  ...props
}: ButtonLinkProps) {
  return (
    <MuiButton
      component="a"
      {...variantProps[variant]}
      size={sizeMap[size]}
      startIcon={leadingIcon}
      endIcon={trailingIcon}
      {...props}
    >
      {children}
    </MuiButton>
  )
}
