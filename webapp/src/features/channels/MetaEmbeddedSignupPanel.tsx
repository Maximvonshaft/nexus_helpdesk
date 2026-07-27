import {
  Alert,
  Button,
  CircularProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
import { launchMetaEmbeddedSignup } from '@/lib/metaEmbeddedSignup'
import { whatsappApi } from '@/lib/whatsappApi'

type Draft = {
  displayName: string
  accountId: string
  marketId: string
  priority: string
}

const emptyDraft: Draft = {
  displayName: '',
  accountId: '',
  marketId: '',
  priority: '100',
}

export function MetaEmbeddedSignupPanel() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [result, setResult] = useState<string | null>(null)

  const signup = useMutation({
    mutationFn: async () => {
      const marketId = draft.marketId.trim() ? Number(draft.marketId) : null
      const priority = Number(draft.priority)
      if (marketId !== null && (!Number.isInteger(marketId) || marketId <= 0)) {
        throw new Error('market_id_invalid')
      }
      if (!Number.isInteger(priority) || priority < 0 || priority > 10_000) {
        throw new Error('priority_invalid')
      }
      const intent = {
        display_name: draft.displayName.trim(),
        account_id: draft.accountId.trim(),
        market_id: marketId,
        priority,
      }
      const session = await whatsappApi.createEmbeddedSignupSession(intent)
      const authorized = await launchMetaEmbeddedSignup(session)
      return whatsappApi.completeEmbeddedSignup(session.session_id, {
        ...intent,
        state: session.state,
        code: authorized.code,
        business_account_id: authorized.finish.business_account_id,
        waba_id: authorized.finish.waba_id,
        phone_number_id: authorized.finish.phone_number_id,
      })
    },
    onSuccess: async (completed) => {
      setResult(`已创建 ${completed.account_id}，连接编号 ${completed.connection_id}，当前进入绑定验证。`)
      setDraft(emptyDraft)
      await queryClient.invalidateQueries({ queryKey: ['whatsappConnections'] })
      await queryClient.invalidateQueries({ queryKey: ['canonicalChannelAccounts'] })
    },
  })

  const ready = Boolean(draft.displayName.trim() && draft.accountId.trim() && !signup.isPending)

  return (
    <Paper component="section" variant="outlined" aria-labelledby="meta-embedded-signup-title" sx={{ mt: 2, p: 2 }}>
      <Typography id="meta-embedded-signup-title" component="h2" variant="h3">Meta Embedded Signup</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        使用 Meta 官方授权窗口接入 WABA 和电话号码。授权 Code 仅传给 Nexus 后端兑换，浏览器不会接触或保存 Access Token。
      </Typography>
      <Stack spacing={1.5} sx={{ mt: 2 }}>
        {signup.error ? <OperatorErrorNotice title="Embedded Signup 失败" error={signup.error} fallback="请检查 Meta App Review、Configuration ID、HTTPS Origin 和授权资产" /> : null}
        {result ? <Alert severity="success" variant="outlined">{result}</Alert> : null}
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
          <TextField required fullWidth label="账号名称" value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
          <TextField required fullWidth label="Nexus 账号标识" helperText="稳定内部标识，例如 wa-meta-ch-primary" value={draft.accountId} onChange={(event) => setDraft((current) => ({ ...current, accountId: event.target.value }))} />
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
          <TextField label="Market ID（可选）" inputMode="numeric" value={draft.marketId} onChange={(event) => setDraft((current) => ({ ...current, marketId: event.target.value }))} />
          <TextField required label="优先级" inputMode="numeric" value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))} />
          <Button
            variant="contained"
            disabled={!ready}
            startIcon={signup.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
            onClick={() => signup.mutate()}
          >
            {signup.isPending ? '正在完成 Meta 授权…' : '通过 Meta 接入'}
          </Button>
        </Stack>
        <Alert severity="info" variant="outlined">
          Meta App 必须完成 App Review，并取得所需权限的 Advanced Access；未满足外部 Meta 条件时，Nexus 会保持 fail-closed，不会创建生产可用账号。
        </Alert>
      </Stack>
    </Paper>
  )
}
