import { CssBaseline, ThemeProvider } from '@mui/material'
import { deDE, enUS, zhCN } from '@mui/material/locale'
import type { Theme } from '@mui/material/styles'
import type { PropsWithChildren } from 'react'
import { useMemo } from 'react'
import { cnrMuiLocale } from '@/i18n/cnrMuiLocale'
import { getUiLocale } from '@/i18n/runtime'
import { nexusTheme } from './nexusTheme'

type ComponentLocaleMap = Record<string, {
  defaultProps?: Record<string, unknown>
  [key: string]: unknown
}>

function applyMuiLocale(theme: Theme, locale: unknown): Theme {
  const localizedComponents = (
    locale && typeof locale === 'object'
      ? (locale as { components?: ComponentLocaleMap }).components
      : undefined
  ) ?? {}
  const themeComponents = (theme.components ?? {}) as ComponentLocaleMap
  const components: ComponentLocaleMap = { ...themeComponents }

  for (const [componentName, localizedValue] of Object.entries(localizedComponents)) {
    const existingValue = themeComponents[componentName] ?? {}
    components[componentName] = {
      ...existingValue,
      ...localizedValue,
      defaultProps: {
        ...(existingValue.defaultProps ?? {}),
        ...(localizedValue.defaultProps ?? {}),
      },
    }
  }

  return {
    ...theme,
    components: components as Theme['components'],
  }
}

export function NexusThemeProvider({ children }: PropsWithChildren) {
  const theme = useMemo(() => {
    const locale = getUiLocale()
    const muiLocale = locale === 'de'
      ? deDE
      : locale === 'en'
        ? enUS
        : locale === 'cnr'
          ? cnrMuiLocale
          : zhCN
    return applyMuiLocale(nexusTheme, muiLocale)
  }, [])

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {children}
    </ThemeProvider>
  )
}
