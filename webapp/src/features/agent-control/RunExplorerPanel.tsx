import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded'
import TimelineRoundedIcon from '@mui/icons-material/TimelineRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import {
  agentRuntimeApi,
  type AgentRun,
  type AgentRuntimeScope,
  type AgentSpecialist,
} from '@/lib/agentRuntimeApi'

const STATUS_OPTIONS: Array<{ value: '' | AgentRun['status']; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'running', label: '运行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'fallback', label: '降级' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const SPECIALISTS: Array<{ value: AgentSpecialist; label: string }> = [
  { value: 'knowledge_researcher', label: '知识研究' },
  { value: 'policy_reviewer', label: '政策复核' },
  { value: 'case_summarizer', label: '案例摘要' },
  { value: 'translation_reviewer', label: '翻译复核' },
  { value: 'data_analyst', label: '数据分析' },
]

export function RunExplorerPanel({
  tenantKey,
  scope,
  canExecute,
}: {
  tenantKey: string
  scope: AgentRuntimeScope
  canExecute: boolean
}) {
  const [status, setStatus] = useState<'' | AgentRun['status']>('')
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [forkBody, setForkBody] = useState('')
  const [forkKind, setForkKind] = useState<'playground' | 'replay'>('replay')
  const [specialists, setSpecialists] = useState<AgentSpecialist[]>([])
  const [executeModel, setExecuteModel] = useState(false)
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
  const fork = useMutation({
    mutationFn: () => agentRuntimeApi.forkRun(selectedRunId as number, {
      tenant_key: tenantKey,
      environment: scope.environment,
      market_id: scope.market_id,
      channel: scope.channel || 'webchat',
      language: scope.language,
      case_type: scope.case_type,
      body: forkBody,
      fork_kind: forkKind,
      cohort_key: `operator-${forkKind}`,
      specialists,
      execute_model: executeModel,
    }),
  })
  useEffect(() => {
    fork.reset()
    setForkBody('')
    setSpecialists([])
    setExecuteModel(false)
  // Reset only when the selected authority changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId])

  const terminalRun = selectedRun && selectedRun.status !== 'running'
  const canRunFork = Boolean(terminalRun && forkBody.trim() && !fork.isPending)

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <TimelineRoundedIcon color="primary" />
        <Typography component="h2" variant="h2">Agent Run Explorer</Typography>
        {runs.isFetching ? <CircularProgress size={18} /> : null}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        查看按 Sequence 追加的运行事件，并基于原不可变 Release 创建只读 Replay 或 Playground Fork。事件不保存原始 Prompt、隐藏推理、凭据、Tool 参数或客户 PII。
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
        <Paper variant="outlined" sx={{ p: 1, maxHeight: 760, overflow: 'auto' }}>
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
                  {run.parent_run_id ? (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {run.fork_kind || 'fork'} of Run #{run.parent_run_id}
                    </Typography>
                  ) : null}
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
            <OperatorEmptyState title="选择一个 Run" description="查看事件时间线并创建只读 Fork" />
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

              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <ReplayRoundedIcon color="primary" />
                  <Typography variant="h3">只读 Replay / Playground Fork</Typography>
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  只在当前作用域解析到与原 Run 相同的 Release ID 和 manifest SHA 时执行。候选 Tool 还会与该 Release 的允许清单取交集，写 Tool 永不进入 Fork。
                </Typography>
                {!terminalRun ? (
                  <Alert severity="info" sx={{ mt: 1.5 }}>运行中的 Run 不能创建 Fork。</Alert>
                ) : null}
                <Box
                  sx={{
                    display: 'grid',
                    gridTemplateColumns: { xs: '1fr', md: '180px minmax(0, 1fr)' },
                    gap: 1.25,
                    mt: 1.5,
                  }}
                >
                  <TextField
                    select
                    size="small"
                    label="Fork 类型"
                    value={forkKind}
                    onChange={(event) => setForkKind(event.target.value as 'playground' | 'replay')}
                    disabled={!terminalRun}
                  >
                    <MenuItem value="replay">Replay</MenuItem>
                    <MenuItem value="playground">Playground</MenuItem>
                  </TextField>
                  <TextField
                    label="新的测试消息"
                    value={forkBody}
                    onChange={(event) => setForkBody(event.target.value)}
                    multiline
                    minRows={3}
                    disabled={!terminalRun}
                  />
                </Box>
                <Typography variant="subtitle2" sx={{ mt: 1.5 }}>允许 Parent Agent 请求的 Specialist（最多三个）</Typography>
                <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                  {SPECIALISTS.map((item) => (
                    <FormControlLabel
                      key={item.value}
                      control={(
                        <Checkbox
                          size="small"
                          checked={specialists.includes(item.value)}
                          disabled={!terminalRun || (!specialists.includes(item.value) && specialists.length >= 3)}
                          onChange={(event) => setSpecialists(toggleSpecialist(specialists, item.value, event.target.checked))}
                        />
                      )}
                      label={item.label}
                    />
                  ))}
                </Stack>
                <FormControlLabel
                  sx={{ mt: 0.5 }}
                  control={(
                    <Checkbox
                      checked={executeModel}
                      disabled={!terminalRun || !canExecute}
                      onChange={(event) => setExecuteModel(event.target.checked)}
                    />
                  )}
                  label={canExecute ? '执行模型（仍为只读 Tool）' : '仅预览；缺少 runtime.manage 权限'}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 1 }}>
                  <Button
                    variant={executeModel ? 'contained' : 'outlined'}
                    startIcon={executeModel ? <PlayArrowRoundedIcon /> : <ReplayRoundedIcon />}
                    disabled={!canRunFork}
                    onClick={() => fork.mutate()}
                  >
                    {executeModel ? '执行只读 Fork' : '预览 Fork 边界'}
                  </Button>
                  {fork.isPending ? <CircularProgress size={24} /> : null}
                </Stack>
                {fork.error ? (
                  <Box sx={{ mt: 1.5 }}>
                    <OperatorErrorNotice
                      title="无法创建 Fork"
                      error={fork.error}
                      fallback="请检查当前作用域是否仍解析到原不可变 Release"
                    />
                  </Box>
                ) : null}
                {fork.data ? (
                  <Stack spacing={1} sx={{ mt: 1.5 }}>
                    <Alert severity={fork.data.error_code ? 'warning' : 'success'}>
                      Release #{fork.data.exact_release_id} · {fork.data.read_tools.length} 个只读 Tool · {fork.data.model_executed ? `生成 Run #${fork.data.agent_run_id || '未知'}` : '仅完成权限预览'}
                    </Alert>
                    {fork.data.reply ? (
                      <Paper variant="outlined" sx={{ p: 1.25 }}>
                        <Typography variant="caption" color="text.secondary">Fork 回复</Typography>
                        <Typography sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>{fork.data.reply}</Typography>
                      </Paper>
                    ) : null}
                    <OperatorTechnicalDisclosure title="Fork 权威证据">
                      <Box component="pre" sx={{ m: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                        {JSON.stringify(fork.data, null, 2)}
                      </Box>
                    </OperatorTechnicalDisclosure>
                  </Stack>
                ) : null}
              </Paper>

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

function toggleSpecialist(
  current: AgentSpecialist[],
  specialist: AgentSpecialist,
  checked: boolean,
) {
  if (checked) return current.includes(specialist) ? current : [...current, specialist].slice(0, 3)
  return current.filter((item) => item !== specialist)
}
