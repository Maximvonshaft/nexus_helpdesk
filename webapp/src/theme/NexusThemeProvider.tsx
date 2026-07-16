import { CssBaseline, ThemeProvider } from '@mui/material'
import type { PropsWithChildren } from 'react'
import { nexusTheme } from './nexusTheme'

export function NexusThemeProvider({ children }: PropsWithChildren) {
  return (
    <ThemeProvider theme={nexusTheme}>
      <CssBaseline />
      {children}
    </ThemeProvider>
  )
}
