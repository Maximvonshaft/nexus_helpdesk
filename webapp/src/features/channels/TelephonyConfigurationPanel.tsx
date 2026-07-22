import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  MenuItem,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { supportApi } from '@/lib/supportApi'
import type { ChannelAccount } from '@/lib/types'
import type { VoiceConfigurationUpdate, VoiceRoutingMode } from '@/lib/telephonyTypes'

const DEFAULT_DRAFT: VoiceConfigurationUpdate = {
  inbound_trunk_id: null,
  outbound_trunk_id: null,
  routing_mode: 'ai_first',
  ai_agent_name: null,
  queue_timeout_seconds: 90,
  wrap_up_seconds: 30,
  recording_policy: 'disabled',
  enabled: false,
}

export function TelephonyConfigurationPanel({ accounts }: { accounts: ChannelAccount[] }) {
  const queryClient = useQueryClient()
  const voiceAccounts = useMemo(() => accounts.filter((item) => item.provider === 'voice'), [accounts])
  const [selectedId, setSelectedId] = useState<number | ''>('')
  const [draft, setDraft] = useState<VoiceConfigurationUpdate>(DEFAULT_DRAFT)
  const configurations = useQuery({
    queryKey: ['voiceConfigurations'],
    queryFn: supportApi.voiceConfigurations,
    retry: false,
  })
  const selected = configurations.data?.items.find((item) => item.channel_account_id === selectedId)

  useEffect(() => {
    if (selectedId === '' && voiceAccounts.length) setSelectedId(voiceAccounts[0].id)
  }, [selectedId, voiceAccounts])

  useEffect(() => {
    if (!selected) {
      setDraft(DEFAULT_DRAFT)
      return
    }
    setDraft({
      inbound_trunk_id: selected.inbound_trunk_id || null,
      outbound_trunk_id: selected.outbound_trunk_id || null,
      routing_mode: selected.routing_mode,
      ai_agent_name: selected.ai_agent_name || null,
      queue_timeout_seconds: selected.queue_timeout_seconds,
      wrap_up_seconds: selected.wrap_up_seconds,
      recording_policy: selected.recording_policy,
      enabled: selected.enabled,
    })
  }, [selected])

  const save = useMutation({
    mutationFn: () => {
      if (selectedId === '') throw new Error('请选择语音渠道账号')
      return supportApi.updateVoiceConfiguration(selectedId, draft)
    },
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['voiceConfigurations'] }),
  })

  return (
    <Paper component="section" variant="outlined" sx={{ p: 2, mt: 2 }} aria-labelledby="telephony-title">
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ justifyContent: 'space-between', alignItems: { md: 'center' } }}>
        <Box>
          <Typography id="telephony-title" component="h2" variant="h3">LiveKit 电话与 SIP</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            电话号码继续使用渠道账号；此处只配置 LiveKit Trunk、AI/人工路由和通话后整理策略。
          </Typography>
        </Box>
        <Chip label="单一 LiveKit 媒体平面" color="primary" variant="outlined" />
      </Stack>
      <Divider sx={{ my: 2 }} />
      {configurations.isLoading ? <CircularProgress size={22} /> : configurations.isError ? (
        <OperatorErrorNotice title="无法读取电话配置" error={configurations.error} fallback="请稍后重试" />
      ) : !voiceAccounts.length ? (
        <OperatorEmptyState title="尚未创建语音渠道账号" description="先在接入任务中创建 provider=voice、绑定号码为 E.164 的渠道账号。" />
      ) : (
        <Stack spacing={1.5}>
          <TextField select label="电话号码账号" value={selectedId} onChange={(event) => setSelectedId(Number(event.target.value))}>
            {voiceAccounts.map((account) => (
              <MenuItem key={account.id} value={account.id}>{account.display_name || account.account_id}</MenuItem>
            ))}
          </TextField>
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
            <TextField label="入站 Trunk ID" value={draft.inbound_trunk_id || ''} onChange={(event) => setDraft((value) => ({ ...value, inbound_trunk_id: event.target.value || null }))} />
            <TextField label="出站 Trunk ID" value={draft.outbound_trunk_id || ''} onChange={(event) => setDraft((value) => ({ ...value, outbound_trunk_id: event.target.value || null }))} />
            <TextField select label="路由模式" value={draft.routing_mode} onChange={(event) => setDraft((value) => ({ ...value, routing_mode: event.target.value as VoiceRoutingMode }))}>
              <MenuItem value="ai_first">AI 优先，按需转人工</MenuItem>
              <MenuItem value="human_first">人工优先</MenuItem>
            </TextField>
            <TextField label="LiveKit Agent Name" disabled={draft.routing_mode !== 'ai_first'} value={draft.ai_agent_name || ''} onChange={(event) => setDraft((value) => ({ ...value, ai_agent_name: event.target.value || null }))} />
            <TextField type="number" label="排队超时（秒）" value={draft.queue_timeout_seconds} slotProps={{ htmlInput: { min: 15, max: 3600 } }} onChange={(event) => setDraft((value) => ({ ...value, queue_timeout_seconds: Number(event.target.value) }))} />
            <TextField type="number" label="通话后整理（秒）" value={draft.wrap_up_seconds} slotProps={{ htmlInput: { min: 0, max: 900 } }} onChange={(event) => setDraft((value) => ({ ...value, wrap_up_seconds: Number(event.target.value) }))} />
          </Box>
          <Alert severity="info" variant="outlined">录音保持关闭；只有国家级告知、同意、保留期与删除策略完成后，才能切换为 consent_required。</Alert>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Switch checked={draft.enabled} onChange={(_, checked) => setDraft((value) => ({ ...value, enabled: checked }))} />
              <Typography>{draft.enabled ? '已启用入站电话路由' : '未启用'}</Typography>
            </Stack>
            <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? '保存中…' : '保存电话配置'}
            </Button>
          </Stack>
          {save.isError ? <OperatorErrorNotice title="保存失败" error={save.error} fallback="检查号码、Trunk 和路由配置" /> : null}
        </Stack>
      )}
    </Paper>
  )
}
