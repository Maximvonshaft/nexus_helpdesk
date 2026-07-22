import CircleRoundedIcon from '@mui/icons-material/CircleRounded'
import {
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import type { SelectChangeEvent } from '@mui/material/Select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import {
  agentRoutingApi,
  type AgentPresenceStatus,
} from '@/lib/agentRoutingApi'

const STATE_QUERY_KEY = ['operatorAgentState'] as const

const LABELS: Record<AgentPresenceStatus, string> = {
  online: '在线',
  paused: '暂停接线',
  offline: '离线',
}

export function AgentPresenceControl({ capabilities }: { capabilities: Set<string> }) {
  const queryClient = useQueryClient()
  const enabled = capabilities.has('webchat.handoff.accept')
  const state = useQuery({
    queryKey: STATE_QUERY_KEY,
    queryFn: agentRoutingApi.state,
    enabled,
    refetchInterval: 30_000,
    retry: false,
  })
  const update = useMutation({
    mutationFn: (status: AgentPresenceStatus) => agentRoutingApi.updateState(
      status,
      state.data?.max_concurrent_conversations,
      state.data?.max_concurrent_voice_calls,
      state.data?.voice_wrap_up_seconds,
    ),
    onSuccess: (next) => queryClient.setQueryData(STATE_QUERY_KEY, next),
  })

  useEffect(() => {
    if (!enabled || !state.data || state.data.status === 'offline') return undefined
    const timer = window.setInterval(() => {
      void agentRoutingApi.heartbeat()
        .then((next) => queryClient.setQueryData(STATE_QUERY_KEY, next))
        .catch(() => undefined)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [enabled, queryClient, state.data])

  if (!enabled) return null
  if (state.isLoading) return <CircularProgress size={20} aria-label="正在读取客服状态" />
  if (!state.data) return <Chip size="small" label="坐席状态不可用" />

  const handleChange = (event: SelectChangeEvent<AgentPresenceStatus>) => {
    update.mutate(event.target.value as AgentPresenceStatus)
  }
  const capacity = `${state.data.active_conversations}/${state.data.max_concurrent_conversations}`
  const voiceCapacity = `${state.data.active_voice_calls}/${state.data.max_concurrent_voice_calls}`
  const available = state.data.status === 'online' && state.data.heartbeat_fresh

  return (
    <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
      <FormControl size="small" sx={{ minWidth: 112, display: { xs: 'none', lg: 'flex' } }}>
        <InputLabel id="nd-agent-presence-label">客服状态</InputLabel>
        <Select<AgentPresenceStatus>
          labelId="nd-agent-presence-label"
          value={state.data.status}
          label="客服状态"
          onChange={handleChange}
          disabled={update.isPending}
          inputProps={{ 'aria-label': '客服状态' }}
        >
          <MenuItem value="online">在线</MenuItem>
          <MenuItem value="paused">暂停接线</MenuItem>
          <MenuItem value="offline">离线</MenuItem>
        </Select>
      </FormControl>
      <Tooltip title={`文字会话 ${capacity}，语音 ${voiceCapacity}，语音整理 ${state.data.voice_wrap_up_seconds} 秒`}>
        <Chip
          size="small"
          icon={<CircleRoundedIcon fontSize="small" />}
          color={available ? 'success' : state.data.status === 'paused' ? 'warning' : 'default'}
          label={state.data.status === 'online' ? `接线 ${capacity} · 语音 ${voiceCapacity}` : LABELS[state.data.status]}
          variant={available ? 'filled' : 'outlined'}
        />
      </Tooltip>
      {!state.data.heartbeat_fresh && state.data.status !== 'offline' ? (
        <Typography variant="caption" color="warning.main" sx={{ display: { xs: 'none', xl: 'block' } }}>
          正在恢复在线心跳
        </Typography>
      ) : null}
    </Stack>
  )
}
