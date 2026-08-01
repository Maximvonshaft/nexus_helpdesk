import { CssBaseline, ThemeProvider } from '@mui/material'
import { deDE, enUS, zhCN } from '@mui/material/locale'
import { createTheme } from '@mui/material/styles'
import type { PropsWithChildren } from 'react'
import { useMemo } from 'react'
import { getUiLocale } from '@/i18n/runtime'
import { nexusTheme } from './nexusTheme'

export function NexusThemeProvider({ children }: PropsWithChildren) {
  const theme = useMemo(() => {
    const locale = getUiLocale()
    const muiLocale = locale === 'de' ? deDE : locale === 'en' ? enUS : zhCN
    return createTheme(nexusTheme, muiLocale)
  }, [])

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {children}
    </ThemeProvider>
  )
}
