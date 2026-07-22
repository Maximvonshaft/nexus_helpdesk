import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
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
import type {
  VoiceBusinessHours,
  VoiceConfigurationUpdate,
  VoiceOverflowAction,
  VoiceRecordingPolicy,
  VoiceRoutingMode,
  VoiceTranscriptionPolicy,
} from '@/lib/telephonyTypes'

const WEEKDAYS = [
  ['monday', '星期一'],
  ['tuesday', '星期二'],
  ['wednesday', '星期三'],
  ['thursday', '星期四'],
  ['friday', '星期五'],
  ['saturday', '星期六'],
  ['sunday', '星期日'],
] as const

type Weekday = (typeof WEEKDAYS)[number][0]

const DEFAULT_BUSINESS_HOURS: VoiceBusinessHours = {
  monday: [{ start: '09:00', end: '18:00' }],
  tuesday: [{ start: '09:00', end: '18:00' }],
  wednesday: [{ start: '09:00', end: '18:00' }],
  thursday: [{ start: '09:00', end: '18:00' }],
  friday: [{ start: '09:00', end: '18:00' }],
}

const DEFAULT_DRAFT: VoiceConfigurationUpdate = {
  livekit_project_ref: null,
  inbound_trunk_id: null,
  outbound_trunk_id: null,
  dispatch_rule_id: null,
  routing_mode: 'ai_first',
  ai_agent_name: null,
  timezone: 'UTC',
  business_hours: DEFAULT_BUSINESS_HOURS,
  queue_timeout_seconds: 90,
  offer_timeout_seconds: 20,
  wrap_up_seconds: 30,
  overflow_action: 'ai',
  voicemail_enabled: false,
  recording_policy: 'disabled',
  transcription_policy: 'disabled',
  enabled: false,
}

function dayWindow(hours: VoiceBusinessHours | null | undefined, day: Weekday) {
  return hours?.[day]?.[0] ?? null
}

function updateDay(
  current: VoiceBusinessHours | null | undefined,
  day: Weekday,
  enabled: boolean,
  field?: 'start' | 'end',
  fieldValue?: string,
): VoiceBusinessHours {
  const next: VoiceBusinessHours = { ...(current ?? {}) }
  if (!enabled) {
    delete next[day]
    return next
  }
  const existing = dayWindow(next, day) ?? { start: '09:00', end: '18:00' }
  next[day] = [{ ...existing, ...(field ? { [field]: fieldValue } : {}) }]
  return next
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
      livekit_project_ref: selected.livekit_project_ref || null,
      inbound_trunk_id: selected.inbound_trunk_id || null,
      outbound_trunk_id: selected.outbound_trunk_id || null,
      dispatch_rule_id: selected.dispatch_rule_id || null,
      routing_mode: selected.routing_mode,
      ai_agent_name: selected.ai_agent_name || null,
      timezone: selected.timezone || 'UTC',
      business_hours: selected.business_hours ?? null,
      queue_timeout_seconds: selected.queue_timeout_seconds,
      offer_timeout_seconds: selected.offer_timeout_seconds,
      wrap_up_seconds: selected.wrap_up_seconds,
      overflow_action: selected.overflow_action,
      voicemail_enabled: selected.voicemail_enabled,
      recording_policy: selected.recording_policy,
      transcription_policy: selected.transcription_policy,
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
          <Typography id="telephony-title" component="h2" variant="h3">电话与实时语音</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            配置 AI/人工接听、工作时间、等待与无人接听策略。电话号码仍由统一渠道账号管理。
          </Typography>
        </Box>
        <Chip label="单一 LiveKit 媒体平面" color="primary" variant="outlined" />
      </Stack>
      <Divider sx={{ my: 2 }} />
      {configurations.isLoading ? <CircularProgress size={22} /> : configurations.isError ? (
        <OperatorErrorNotice title="无法读取电话配置" error={configurations.error} fallback="请稍后重试" />
      ) : !voiceAccounts.length ? (
        <OperatorEmptyState title="尚未创建语音渠道账号" description="先在接入任务中创建语音渠道账号并绑定电话号码。" />
      ) : (
        <Stack spacing={2}>
          <TextField select label="电话号码" value={selectedId} onChange={(event) => setSelectedId(Number(event.target.value))}>
            {voiceAccounts.map((account) => (
              <MenuItem key={account.id} value={account.id}>{account.display_name || account.account_id}</MenuItem>
            ))}
          </TextField>

          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
            <TextField select label="接听规则" value={draft.routing_mode} onChange={(event) => setDraft((value) => ({ ...value, routing_mode: event.target.value as VoiceRoutingMode }))}>
              <MenuItem value="ai_first">AI 先接听，按需转人工</MenuItem>
              <MenuItem value="human_first">优先寻找人工坐席</MenuItem>
            </TextField>
            <TextField label="号码时区" value={draft.timezone} onChange={(event) => setDraft((value) => ({ ...value, timezone: event.target.value || 'UTC' }))} helperText="例如 Europe/Podgorica" />
            <TextField type="number" label="客户最长等待（秒）" value={draft.queue_timeout_seconds} slotProps={{ htmlInput: { min: 15, max: 3600 } }} onChange={(event) => setDraft((value) => ({ ...value, queue_timeout_seconds: Number(event.target.value) }))} />
            <TextField type="number" label="单次坐席邀请（秒）" value={draft.offer_timeout_seconds} slotProps={{ htmlInput: { min: 5, max: 120 } }} onChange={(event) => setDraft((value) => ({ ...value, offer_timeout_seconds: Number(event.target.value) }))} />
            <TextField type="number" label="通话后整理（秒）" value={draft.wrap_up_seconds} slotProps={{ htmlInput: { min: 0, max: 900 } }} onChange={(event) => setDraft((value) => ({ ...value, wrap_up_seconds: Number(event.target.value) }))} />
            <TextField select label="无人接听" value={draft.overflow_action} onChange={(event) => setDraft((value) => ({ ...value, overflow_action: event.target.value as VoiceOverflowAction }))}>
              <MenuItem value="ai">继续由 AI 服务</MenuItem>
              <MenuItem value="voicemail">转语音留言</MenuItem>
              <MenuItem value="disconnect">说明原因后结束通话</MenuItem>
            </TextField>
            <TextField select label="录音策略" value={draft.recording_policy} onChange={(event) => setDraft((value) => ({ ...value, recording_policy: event.target.value as VoiceRecordingPolicy }))}>
              <MenuItem value="disabled">关闭</MenuItem>
              <MenuItem value="consent_required">客户同意后录音</MenuItem>
              <MenuItem value="always">始终录音（仅限已批准市场）</MenuItem>
            </TextField>
            <TextField select label="转写策略" value={draft.transcription_policy} onChange={(event) => setDraft((value) => ({ ...value, transcription_policy: event.target.value as VoiceTranscriptionPolicy }))}>
              <MenuItem value="disabled">关闭</MenuItem>
              <MenuItem value="consent_required">客户同意后转写</MenuItem>
              <MenuItem value="always">始终转写（仅限已批准市场）</MenuItem>
            </TextField>
          </Box>

          <Box>
            <Typography component="h3" variant="h4" sx={{ mb: 1 }}>工作时间</Typography>
            <Stack spacing={1}>
              {WEEKDAYS.map(([day, label]) => {
                const window = dayWindow(draft.business_hours, day)
                return (
                  <Stack key={day} direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' } }}>
                    <FormControlLabel
                      sx={{ minWidth: 120 }}
                      control={<Switch checked={Boolean(window)} onChange={(_, checked) => setDraft((value) => ({ ...value, business_hours: updateDay(value.business_hours, day, checked) }))} />}
                      label={label}
                    />
                    <TextField disabled={!window} type="time" label="开始" value={window?.start ?? '09:00'} onChange={(event) => setDraft((value) => ({ ...value, business_hours: updateDay(value.business_hours, day, true, 'start', event.target.value) }))} slotProps={{ inputLabel: { shrink: true } }} />
                    <TextField disabled={!window} type="time" label="结束" value={window?.end ?? '18:00'} onChange={(event) => setDraft((value) => ({ ...value, business_hours: updateDay(value.business_hours, day, true, 'end', event.target.value) }))} slotProps={{ inputLabel: { shrink: true } }} />
                  </Stack>
                )
              })}
            </Stack>
          </Box>

          {draft.overflow_action === 'voicemail' ? (
            <FormControlLabel control={<Switch checked={draft.voicemail_enabled} onChange={(_, checked) => setDraft((value) => ({ ...value, voicemail_enabled: checked }))} />} label="启用语音留言" />
          ) : null}

          <Alert severity="info" variant="outlined">
            录音或转写只有在对应国家的告知、同意、访问权限、保留期和删除策略完成审批后才能启用。
          </Alert>

          <Accordion variant="outlined" disableGutters>
            <AccordionSummary>
              <Box>
                <Typography component="h3" variant="h4">高级 Provider 诊断</Typography>
                <Typography variant="body2" color="text.secondary">仅供实施与故障排查；日常运营无需修改。</Typography>
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
                <TextField label="LiveKit Project Reference" value={draft.livekit_project_ref || ''} onChange={(event) => setDraft((value) => ({ ...value, livekit_project_ref: event.target.value || null }))} />
                <TextField label="Room Controller / AI Agent Name" value={draft.ai_agent_name || ''} onChange={(event) => setDraft((value) => ({ ...value, ai_agent_name: event.target.value || null }))} />
                <TextField label="Inbound Trunk ID" value={draft.inbound_trunk_id || ''} onChange={(event) => setDraft((value) => ({ ...value, inbound_trunk_id: event.target.value || null }))} />
                <TextField label="Outbound Trunk ID" value={draft.outbound_trunk_id || ''} onChange={(event) => setDraft((value) => ({ ...value, outbound_trunk_id: event.target.value || null }))} />
                <TextField label="Dispatch Rule ID" value={draft.dispatch_rule_id || ''} onChange={(event) => setDraft((value) => ({ ...value, dispatch_rule_id: event.target.value || null }))} />
              </Box>
            </AccordionDetails>
          </Accordion>

          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Switch checked={draft.enabled} onChange={(_, checked) => setDraft((value) => ({ ...value, enabled: checked }))} />
              <Typography>{draft.enabled ? '已启用入站电话路由' : '未启用'}</Typography>
            </Stack>
            <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? '保存中…' : '保存电话配置'}
            </Button>
          </Stack>
          {save.isError ? <OperatorErrorNotice title="保存失败" error={save.error} fallback="检查号码、接听规则和高级 Provider 配置" /> : null}
        </Stack>
      )}
    </Paper>
  )
}
