import TimelineRoundedIcon from '@mui/icons-material/TimelineRounded'
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import {
  agentRuntimeApi,
  type AgentRun,
} from '@/lib/agentRuntimeApi'

const STATUS_OPTIONS: Array<{ value: '' | AgentRun['status']; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'running', label: '运行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'fallback', label: '降级' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

export function RunExplorerPanel({ tenantKey }: { tenantKey: string }) {
  const [status, setStatus] = useState<'' | AgentRun['status']>('')
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const runs = useQuery({
    queryKey: ['agentRuntimeRuns', tenantKey, status],
    queryFn: () => agentRuntimeApi.runs(tenantKey, status || undefined, 100),
    refetchInterval: 10_000,
    retry: false,
  })
  useEffect(() => {
    const items = runs.data || []
    if (!selectedRunId || !items.some((item) => item.id === selectedRunId)) {
      setSelectedRunId(items[0]?.id ?? null)
    }
  }, [runs.data, selectedRunId])
  const selectedRun = useMemo(
    () => (runs.data || []).find((item) => item.id === selectedRunId) || null,
    [runs.data, selectedRunId],
  )
  const events = useQuery({
    queryKey: ['agentRuntimeRunEvents', tenantKey, selectedRunId],
    queryFn: () => agentRuntimeApi.runEvents(selectedRunId as number, tenantKey),
    enabled: selectedRunId != null,
    refetchInterval: selectedRun?.status === 'running' ? 2_000 : false,
    retry: false,
  })

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <TimelineRoundedIcon color="primary" />
        <Typography component="h2" variant="h2">Agent Run Explorer</Typography>
        {runs.isFetching ? <CircularProgress size={18} /> : null}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        查看按 Sequence 追加的运行事件。事件只包含经过闭合 Schema 与脱敏规则允许的证据，不保存原始 Prompt、隐藏推理、凭据、Tool 参数或客户 PII。
      </Typography>
      <TextField
        select
        size="small"
        label="运行状态"
        value={status}
        onChange={(event) => setStatus(event.target.value as '' | AgentRun['status'])}
        sx={{ mt: 2, minWidth: 180 }}
      >
        {STATUS_OPTIONS.map((item) => (
          <MenuItem key={item.value || 'all'} value={item.value}>{item.label}</MenuItem>
        ))}
      </TextField>

      {runs.error ? (
        <Box sx={{ mt: 2 }}>
          <OperatorErrorNotice title="无法读取 Agent Runs" error={runs.error} fallback="请检查运行事件服务" />
        </Box>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', xl: '340px minmax(0, 1fr)' },
          gap: 2,
          mt: 2,
        }}
      >
        <Paper variant="outlined" sx={{ p: 1, maxHeight: 640, overflow: 'auto' }}>
          {(runs.data || []).length ? (
            <List disablePadding>
              {(runs.data || []).map((run) => (
                <ListItemButton
                  key={run.id}
                  selected={run.id === selectedRunId}
                  onClick={() => setSelectedRunId(run.id)}
                  sx={{ display: 'block', borderBottom: 1, borderColor: 'divider' }}
                >
                  <Stack direction="row" spacing={1} sx={{ justifyContent: 'space-between' }}>
                    <Typography variant="subtitle2">Run #{run.id}</Typography>
                    <Chip
                      size="small"
                      color={
                        run.status === 'succeeded'
                          ? 'success'
                          : run.status === 'fallback'
                            ? 'warning'
                            : run.status === 'failed'
                              ? 'error'
                              : run.status === 'running'
                                ? 'info'
                                : 'default'
                      }
                      label={run.status}
                    />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                    Release {run.release_id ?? '未解析'} · {run.elapsed_ms} ms
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {new Date(run.started_at).toLocaleString()}
                  </Typography>
                </ListItemButton>
              ))}
            </List>
          ) : runs.isLoading ? (
            <Stack sx={{ minHeight: 180, alignItems: 'center', justifyContent: 'center' }}>
              <CircularProgress size={24} />
            </Stack>
          ) : (
            <OperatorEmptyState title="尚无 Agent Run" description="模型执行后会在此生成运行证据" />
          )}
        </Paper>

        <Box sx={{ minWidth: 0 }}>
          {!selectedRun ? (
            <OperatorEmptyState title="选择一个 Run" description="查看完整事件时间线" />
          ) : events.error ? (
            <OperatorErrorNotice title="无法读取 Run 事件" error={events.error} fallback="请检查事件持久化" />
          ) : events.isLoading || !events.data ? (
            <Stack sx={{ minHeight: 240, alignItems: 'center', justifyContent: 'center' }}>
              <CircularProgress size={28} />
            </Stack>
          ) : (
            <Stack spacing={1.25}>
              <Alert severity={selectedRun.status === 'failed' ? 'error' : selectedRun.status === 'fallback' ? 'warning' : 'info'}>
                Trace {selectedRun.trace_id.slice(0, 16)} · {events.data.events.length} events · 最终动作 {selectedRun.final_action || '未完成'}
              </Alert>
              {events.data.events.map((event) => (
                <Paper key={event.id} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack
                    direction={{ xs: 'column', sm: 'row' }}
                    spacing={1}
                    sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' } }}
                  >
                    <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                      <Chip size="small" label={`#${event.sequence}`} />
                      <Typography variant="subtitle2">{event.event_type}</Typography>
                      <Chip size="small" variant="outlined" label={event.status} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      {event.duration_ms} ms · {new Date(event.created_at).toLocaleString()}
                    </Typography>
                  </Stack>
                  {Object.keys(event.safe_payload || {}).length ? (
                    <Box component="pre" sx={{ m: 0, mt: 1, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {JSON.stringify(event.safe_payload, null, 2)}
                    </Box>
                  ) : null}
                </Paper>
              ))}
              <OperatorTechnicalDisclosure title="Run 权威摘要">
                <Box component="pre" sx={{ m: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                  {JSON.stringify(events.data.run, null, 2)}
                </Box>
              </OperatorTechnicalDisclosure>
            </Stack>
          )}
        </Box>
      </Box>
    </Paper>
  )
}
