import {
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material'
import type { SelectChangeEvent } from '@mui/material/Select'
import { useMutation } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { uiPreferenceApi } from '@/lib/uiPreferenceApi'
import {
  enabledUiLocales,
  getUiLocale,
  normalizeUiLocale,
  setUiLocale,
} from './runtime'
import type { UiLocale } from './runtime'

const localeNames: Record<UiLocale, string> = {
  'zh-CN': '中文（简体）',
  en: 'English',
  de: 'Deutsch',
  cnr: 'Crnogorski',
}

const controlCopy: Record<UiLocale, { label: string; error: string }> = {
  'zh-CN': { label: '界面语言', error: '语言设置保存失败，请重试。' },
  en: { label: 'Interface language', error: 'The language setting could not be saved. Try again.' },
  de: { label: 'Oberflächensprache', error: 'Die Spracheinstellung konnte nicht gespeichert werden. Versuchen Sie es erneut.' },
  cnr: { label: 'Jezik interfejsa', error: 'Podešavanje jezika nije sačuvano. Pokušajte ponovo.' },
}

export function LanguageControl({
  authenticated = false,
  compact = false,
  fullWidth = false,
}: {
  authenticated?: boolean
  compact?: boolean
  fullWidth?: boolean
}) {
  const generatedId = useId().replaceAll(':', '')
  const labelId = `nexus-ui-language-${generatedId}`
  const activeLocale = getUiLocale()
  const [selectedLocale, setSelectedLocale] = useState<UiLocale>(activeLocale)
  const [storageError, setStorageError] = useState(false)
  const mutation = useMutation({
    mutationFn: uiPreferenceApi.updateLocale,
    onSuccess: (response) => {
      const result = setUiLocale(response.ui_locale)
      if (!result.applied) {
        setSelectedLocale(activeLocale)
        setStorageError(true)
      }
    },
    onError: () => setSelectedLocale(activeLocale),
  })
  const copy = controlCopy[activeLocale]

  const handleChange = (event: SelectChangeEvent<string>) => {
    const nextLocale = normalizeUiLocale(event.target.value)
    if (!nextLocale || !enabledUiLocales.includes(nextLocale)) return
    setStorageError(false)
    setSelectedLocale(nextLocale)
    if (authenticated) {
      mutation.mutate(nextLocale)
      return
    }
    const result = setUiLocale(nextLocale)
    if (!result.applied) {
      setSelectedLocale(activeLocale)
      setStorageError(true)
    }
  }

  const hasError = mutation.isError || storageError

  return (
    <FormControl
      fullWidth={fullWidth}
      error={hasError}
      sx={{ minWidth: compact ? 132 : 200, width: fullWidth ? '100%' : 'auto' }}
    >
      <InputLabel id={labelId}>{copy.label}</InputLabel>
      <Select
        labelId={labelId}
        label={copy.label}
        value={selectedLocale}
        disabled={mutation.isPending}
        onChange={handleChange}
        inputProps={{ 'aria-label': copy.label }}
      >
        {enabledUiLocales.map((locale) => (
          <MenuItem key={locale} value={locale} lang={locale}>
            {localeNames[locale]}
          </MenuItem>
        ))}
      </Select>
      {hasError ? <FormHelperText>{copy.error}</FormHelperText> : null}
    </FormControl>
  )
}
