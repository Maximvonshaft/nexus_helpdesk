import CircleRoundedIcon from '@mui/icons-material/CircleRounded'
import {
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material'
import type { SelectChangeEvent } from '@mui/material/Select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useContext,
  useEffect,
  type ChangeEvent,
  type ReactNode,
} from 'react'
import {
  agentRoutingApi,
  type AgentPresenceStatus,
  type AgentState,
  type AgentStateUpdate,
} from '@/lib/agentRoutingApi'

const STATE_QUERY_KEY = ['operatorAgentState'] as const
const VOICE_CAPABILITIES = [
  'webcall.voice.read',
  'webcall.voice.queue.view',
  'webcall.voice.accept',
  'webcall.voice.reject',
  'webcall.voice.end',
  'webcall.voice.control',
] as const

const LABELS: Record<AgentPresenceStatus, string> = {
  online: '在线',
  paused: '暂停接线',
  offline: '离线',
}

type AgentPresencePresentation = 'toolbar' | 'drawer'

interface AgentPresenceRuntimeValue {
  enabled: boolean
  canViewVoice: boolean
  canHandleVoice: boolean
  state?: AgentState
  isLoading: boolean
  isUpdating: boolean
  hasUpdateError: boolean
  setStatus: (status: AgentPresenceStatus) => void
  setVoiceEnabled: (enabled: boolean) => void
}

const AgentPresenceRuntimeContext = createContext<AgentPresenceRuntimeValue | null>(null)

export function AgentPresenceProvider({
  capabilities,
  children,
}: {
  capabilities: Set<string>
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const canHandleText = capabilities.has('webchat.handoff.accept')
  const canViewVoice = capabilities.has('webcall.voice.queue.view')
  const canHandleVoice = VOICE_CAPABILITIES.every((capability) => capabilities.has(capability))
  const enabled = canHandleText || canViewVoice
  const state = useQuery({
    queryKey: STATE_QUERY_KEY,
    queryFn: agentRoutingApi.state,
    enabled,
    refetchInterval: 30_000,
    retry: false,
  })
  const update = useMutation({
    mutationFn: (request: AgentStateUpdate) => agentRoutingApi.updateState(request),
    onSuccess: (next) => queryClient.setQueryData(STATE_QUERY_KEY, next),
  })
  const status = state.data?.status

  useEffect(() => {
    if (!enabled || !status || status === 'offline') return undefined
    const timer = window.setInterval(() => {
      void agentRoutingApi.heartbeat()
        .then((next) => queryClient.setQueryData(STATE_QUERY_KEY, next))
        .catch(() => undefined)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [enabled, queryClient, status])

  const value: AgentPresenceRuntimeValue = {
    enabled,
    canViewVoice,
    canHandleVoice,
    state: state.data,
    isLoading: state.isLoading,
    isUpdating: update.isPending,
    hasUpdateError: update.isError,
    setStatus: (nextStatus) => update.mutate({ status: nextStatus }),
    setVoiceEnabled: (voiceEnabled) => {
      if (!state.data) return
      update.mutate({ status: state.data.status, voiceEnabled })
    },
  }

  return (
    <AgentPresenceRuntimeContext.Provider value={value}>
      {children}
    </AgentPresenceRuntimeContext.Provider>
  )
}

export function AgentPresenceControl({
  presentation = 'toolbar',
}: {
  presentation?: AgentPresencePresentation
}) {
  const runtime = useContext(AgentPresenceRuntimeContext)
  if (!runtime) throw new Error('AgentPresenceControl requires AgentPresenceProvider')
  if (!runtime.enabled) return null

  const drawer = presentation === 'drawer'
  if (runtime.isLoading) return <CircularProgress size={20} aria-label="正在读取客服状态" />
  if (!runtime.state) return <Chip size="small" label="坐席状态不可用" />

  const handleChange = (event: SelectChangeEvent<AgentPresenceStatus>) => {
    runtime.setStatus(event.target.value as AgentPresenceStatus)
  }
  const handleVoiceChange = (_event: ChangeEvent<HTMLInputElement>, checked: boolean) => {
    runtime.setVoiceEnabled(checked)
  }
  const capacity = `${runtime.state.active_conversations}/${runtime.state.max_concurrent_conversations}`
  const voiceCapacity = `${runtime.state.active_voice_calls}/${runtime.state.max_concurrent_voice_calls}`
  const available = runtime.state.status === 'online' && runtime.state.heartbeat_fresh
  const voiceDisableBlocked = runtime.state.voice_enabled && runtime.state.active_voice_calls > 0
  const voiceSwitchTitle = !runtime.canHandleVoice
    ? '当前账号未获得完整电话接线权限'
    : voiceDisableBlocked
      ? '活动通话结束并完成整理后才能关闭电话接线'
      : runtime.state.voice_enabled
        ? '关闭电话接线'
        : '开启电话接线'
  const voiceStateLabel = runtime.state.voice_enabled ? `语音 ${voiceCapacity}` : '电话关闭'
  const labelId = drawer ? 'nd-drawer-agent-presence-label' : 'nd-agent-presence-label'

  return (
    <Stack
      direction={drawer ? 'column' : 'row'}
      spacing={drawer ? 1.25 : 0.75}
      sx={{ alignItems: drawer ? 'stretch' : 'center', width: drawer ? '100%' : 'auto' }}
    >
      <FormControl size="small" sx={{ minWidth: drawer ? 0 : 112, width: drawer ? '100%' : 'auto' }}>
        <InputLabel id={labelId}>客服状态</InputLabel>
        <Select<AgentPresenceStatus>
          labelId={labelId}
          value={runtime.state.status}
          label="客服状态"
          onChange={handleChange}
          disabled={runtime.isUpdating}
          inputProps={{ 'aria-label': '客服状态' }}
        >
          <MenuItem value="online">在线</MenuItem>
          <MenuItem value="paused">暂停接线</MenuItem>
          <MenuItem value="offline">离线</MenuItem>
        </Select>
      </FormControl>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center', flexWrap: 'wrap', rowGap: 0.75 }}>
        {runtime.canViewVoice ? (
          <Tooltip title={voiceSwitchTitle}>
            <span>
              <Switch
                size="small"
                checked={runtime.state.voice_enabled}
                onChange={handleVoiceChange}
                disabled={!runtime.canHandleVoice || runtime.isUpdating || voiceDisableBlocked}
                slotProps={{ input: { 'aria-label': runtime.state.voice_enabled ? '关闭电话接线' : '开启电话接线' } }}
                sx={drawer ? undefined : { display: { xs: 'none', sm: 'inline-flex' } }}
              />
            </span>
          </Tooltip>
        ) : null}
        <Tooltip title={`文字会话 ${capacity}，${voiceStateLabel}，待接来电 ${runtime.state.reserved_voice_offers}，语音整理 ${runtime.state.voice_wrap_up_seconds} 秒`}>
          <Chip
            size="small"
            icon={<CircleRoundedIcon fontSize="small" />}
            color={available ? 'success' : runtime.state.status === 'paused' ? 'warning' : 'default'}
            label={runtime.state.status === 'online' ? `接线 ${capacity} · ${voiceStateLabel}` : LABELS[runtime.state.status]}
            variant={available ? 'filled' : 'outlined'}
          />
        </Tooltip>
      </Stack>
      {runtime.hasUpdateError ? (
        <Typography variant="caption" color="error.main" sx={{ display: drawer ? 'block' : { xs: 'none', xl: 'block' } }}>
          状态更新失败
        </Typography>
      ) : !runtime.state.heartbeat_fresh && runtime.state.status !== 'offline' ? (
        <Typography variant="caption" color="warning.main" sx={{ display: drawer ? 'block' : { xs: 'none', xl: 'block' } }}>
          正在恢复在线心跳
        </Typography>
      ) : null}
    </Stack>
  )
}
