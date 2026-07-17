import {
  Alert,
  AlertTitle,
  Box,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'
import type { AlertColor } from '@mui/material'
import type { ReactNode } from 'react'

export type OperatorTone = 'default' | 'success' | 'warning' | 'danger'

export function operatorToneColor(tone: OperatorTone | string | null | undefined): Exclude<AlertColor, 'info'> | 'default' {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'error'
  return 'default'
}

export function operatorTonePalettePath(tone: OperatorTone | string | null | undefined) {
  if (tone === 'success') return 'success.main'
  if (tone === 'warning') return 'warning.main'
  if (tone === 'danger') return 'error.main'
  return 'text.secondary'
}

export function operatorErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function operatorScrollBehavior(): ScrollBehavior {
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return 'auto'
  return 'smooth'
}

export function OperatorLoadingState({ label, minHeight = 150 }: { label: string; minHeight?: number | string }) {
  return (
    <Stack role="status" aria-live="polite" alignItems="center" justifyContent="center" spacing={1.5} sx={{ minHeight, p: 3 }}>
      <CircularProgress size={28} />
      <Typography variant="subtitle2">{label}</Typography>
    </Stack>
  )
}

export function RouteLoadingState({ label }: { label: string }) {
  return <OperatorLoadingState label={label} minHeight="52vh" />
}

export function OperatorEmptyState({
  title,
  description,
  action,
  minHeight = 140,
}: {
  title: string
  description?: string
  action?: ReactNode
  minHeight?: number | string
}) {
  return (
    <Stack role="status" alignItems="center" justifyContent="center" spacing={0.75} sx={{ minHeight, p: 3, textAlign: 'center' }}>
      <Typography variant="subtitle2">{title}</Typography>
      {description ? <Typography variant="body2" color="text.secondary">{description}</Typography> : null}
      {action ? <Box sx={{ pt: 0.75 }}>{action}</Box> : null}
    </Stack>
  )
}

export function OperatorErrorNotice({
  title,
  error,
  fallback,
  action,
}: {
  title: string
  error: unknown
  fallback: string
  action?: ReactNode
}) {
  return (
    <Alert severity="error" variant="outlined" action={action}>
      <AlertTitle>{title}</AlertTitle>
      {operatorErrorMessage(error, fallback)}
    </Alert>
  )
}

export function OperatorFactGrid({
  facts,
  columns = 4,
}: {
  facts: Array<[string, ReactNode]>
  columns?: number
}) {
  return (
    <Box
      component="dl"
      sx={{
        display: 'grid',
        gap: 1.5,
        gridTemplateColumns: {
          xs: '1fr',
          sm: 'repeat(2, minmax(0, 1fr))',
          md: `repeat(${Math.max(1, columns)}, minmax(0, 1fr))`,
        },
        m: 0,
      }}
    >
      {facts.map(([label, value]) => (
        <Box key={label} sx={{ minWidth: 0 }}>
          <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
          <Typography
            component="dd"
            variant="body2"
            sx={{ m: 0, mt: 0.5, overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}
          >
            {value}
          </Typography>
        </Box>
      ))}
    </Box>
  )
}
