import { Alert, AlertTitle, Box } from '@mui/material'
import type { ReactNode } from 'react'

export function ErrorSummary({ title = '请先处理以下问题', errors, action }: { title?: string; errors: string[]; action?: ReactNode }) {
  if (!errors.length) return null
  return (
    <Alert
      severity="error"
      variant="outlined"
      role="alert"
      aria-live="assertive"
      action={action}
    >
      <AlertTitle>{title}</AlertTitle>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {errors.map((error) => <li key={error}>{error}</li>)}
      </Box>
    </Alert>
  )
}
