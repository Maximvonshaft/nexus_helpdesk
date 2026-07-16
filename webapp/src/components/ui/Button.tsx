import { Button as MuiButton, CircularProgress } from '@mui/material'
import { forwardRef } from 'react'
import type { ButtonProps as MuiButtonProps } from '@mui/material/Button'
import type { ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export type ButtonProps = Omit<MuiButtonProps, 'variant' | 'size' | 'startIcon' | 'endIcon'> & {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  loadingLabel?: string
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
}

const variantProps: Record<ButtonVariant, Pick<MuiButtonProps, 'variant' | 'color'>> = {
  primary: { variant: 'contained', color: 'primary' },
  secondary: { variant: 'outlined', color: 'inherit' },
  ghost: { variant: 'text', color: 'inherit' },
  danger: { variant: 'contained', color: 'error' },
}

const sizeMap: Record<ButtonSize, MuiButtonProps['size']> = {
  sm: 'small',
  md: 'medium',
  lg: 'large',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  children,
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
  return (
    <MuiButton
      ref={ref}
      type={type}
      {...variantProps[variant]}
      size={sizeMap[size]}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      startIcon={loading ? <CircularProgress color="inherit" size={16} /> : leadingIcon}
      endIcon={!loading ? trailingIcon : undefined}
      {...rest}
    >
      {loading ? loadingLabel : children}
    </MuiButton>
  )
})
