import { Alert, Button, Divider, Paper, Stack, Typography } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { uiPreferenceApi } from '@/lib/uiPreferenceApi'
import { readRecoveryUiLocale } from './localeAuthority'
import { getUiLocale, setUiLocale } from './runtime'
import type { UiLocale } from './runtime'
import { LanguageControl } from './LanguageControl'

const copy: Record<UiLocale, {
  title: string
  description: string
  recovery: string
  saveRecovery: string
  recoverySaved: string
  recoveryError: string
}> = {
  'zh-CN': {
    title: '界面语言',
    description: '该设置会同步到账号，并应用于此账号后续登录的设备。客户消息、工单内容和审计证据不会被翻译。',
    recovery: '当前正在使用临时中文恢复界面。账户中保存的语言尚未更改。',
    saveRecovery: '将当前中文保存到账户',
    recoverySaved: '中文已保存为账户界面语言。',
    recoveryError: '无法保存恢复语言，请重试。',
  },
  en: {
    title: 'Interface language',
    description: 'This setting is saved to your account and applies on future sign-ins. Customer messages, ticket content and audit evidence are never translated.',
    recovery: 'A temporary recovery language is active. The saved account language has not changed.',
    saveRecovery: 'Save the current language to the account',
    recoverySaved: 'The current language was saved to the account.',
    recoveryError: 'The recovery language could not be saved. Try again.',
  },
  de: {
    title: 'Oberflächensprache',
    description: 'Diese Einstellung wird in Ihrem Konto gespeichert und bei zukünftigen Anmeldungen verwendet. Kundennachrichten, Ticketinhalte und Prüfnachweise werden niemals übersetzt.',
    recovery: 'Eine temporäre Wiederherstellungssprache ist aktiv. Die gespeicherte Kontosprache wurde nicht geändert.',
    saveRecovery: 'Aktuelle Sprache im Konto speichern',
    recoverySaved: 'Die aktuelle Sprache wurde im Konto gespeichert.',
    recoveryError: 'Die Wiederherstellungssprache konnte nicht gespeichert werden. Versuchen Sie es erneut.',
  },
  cnr: {
    title: 'Jezik interfejsa',
    description: 'Ovo podešavanje se čuva na vašem nalogu i primjenjuje pri budućim prijavama. Poruke korisnika, sadržaj tiketa i revizijski dokazi nikada se ne prevode.',
    recovery: 'Aktivan je privremeni jezik za oporavak. Sačuvani jezik naloga nije promijenjen.',
    saveRecovery: 'Sačuvaj trenutni jezik na nalogu',
    recoverySaved: 'Trenutni jezik je sačuvan na nalogu.',
    recoveryError: 'Jezik za oporavak nije moguće sačuvati. Pokušajte ponovo.',
  },
}

export function AccountLanguagePanel() {
  const activeLocale = getUiLocale()
  const activeCopy = copy[activeLocale]
  const [recoveryActive, setRecoveryActive] = useState(() => Boolean(readRecoveryUiLocale()))
  const recoveryMutation = useMutation({
    mutationFn: () => uiPreferenceApi.updateLocale(activeLocale),
    onSuccess: (response) => {
      const result = setUiLocale(response.ui_locale, { reload: false })
      if (result.applied) setRecoveryActive(false)
    },
  })

  return (
    <Paper component="section" variant="outlined" aria-labelledby="account-language-title" sx={{ p: 2, mt: 2 }}>
      <Typography id="account-language-title" component="h2" variant="h3">
        {activeCopy.title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        {activeCopy.description}
      </Typography>
      <Divider sx={{ my: 2 }} />
      <Stack spacing={1.5} sx={{ maxWidth: 420 }}>
        <LanguageControl authenticated fullWidth />
        {recoveryActive ? (
          <Alert
            severity={recoveryMutation.isError ? 'error' : 'warning'}
            action={(
              <Button
                color="inherit"
                size="small"
                disabled={recoveryMutation.isPending}
                onClick={() => recoveryMutation.mutate()}
              >
                {activeCopy.saveRecovery}
              </Button>
            )}
          >
            {recoveryMutation.isError ? activeCopy.recoveryError : activeCopy.recovery}
          </Alert>
        ) : recoveryMutation.isSuccess ? (
          <Alert severity="success">{activeCopy.recoverySaved}</Alert>
        ) : null}
      </Stack>
    </Paper>
  )
}
