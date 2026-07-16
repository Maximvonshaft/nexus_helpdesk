import { Chip } from '@mui/material'
import type { ReactNode } from 'react'
import type { BadgeTone } from '@/lib/types'

const colorByTone = {
  default: 'default',
  warning: 'warning',
  success: 'success',
  danger: 'error',
} as const

export function Badge({ children, tone = 'default' }: { children: ReactNode; tone?: BadgeTone }) {
  return (
    <Chip
      component="span"
      color={colorByTone[tone]}
      label={children}
      variant={tone === 'default' ? 'outlined' : 'filled'}
    />
  )
}
