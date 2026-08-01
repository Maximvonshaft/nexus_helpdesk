import { Divider, Paper, Stack, Typography } from '@mui/material'
import { getUiLocale } from './runtime'
import type { UiLocale } from './runtime'
import { LanguageControl } from './LanguageControl'

const copy: Record<UiLocale, { title: string; description: string }> = {
  'zh-CN': {
    title: '界面语言',
    description: '该设置会同步到账号，并应用于此账号后续登录的设备。客户消息、工单内容和审计证据不会被翻译。',
  },
  en: {
    title: 'Interface language',
    description: 'This setting is saved to your account and applies on future sign-ins. Customer messages, ticket content and audit evidence are never translated.',
  },
  de: {
    title: 'Oberflächensprache',
    description: 'Diese Einstellung wird in Ihrem Konto gespeichert und bei zukünftigen Anmeldungen verwendet. Kundennachrichten, Ticketinhalte und Prüfnachweise werden niemals übersetzt.',
  },
}

export function AccountLanguagePanel() {
  const activeCopy = copy[getUiLocale()]
  return (
    <Paper component="section" variant="outlined" aria-labelledby="account-language-title" sx={{ p: 2, mt: 2 }}>
      <Typography id="account-language-title" component="h2" variant="h3">
        {activeCopy.title}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        {activeCopy.description}
      </Typography>
      <Divider sx={{ my: 2 }} />
      <Stack sx={{ maxWidth: 420 }}>
        <LanguageControl authenticated fullWidth />
      </Stack>
    </Paper>
  )
}
