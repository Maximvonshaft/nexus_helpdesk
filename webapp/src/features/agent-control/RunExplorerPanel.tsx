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
  { value: 'running', label: '处理中' },
  { value: 'succeeded', label: '已完成' },
  { value: 'fallback', label: '已降级' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const SPECIALISTS: Array<{ value: AgentSpecialist; label: string }> = [
  { value: 'knowledge_researcher', label: '补充知识检索' },
  { value: 'policy_reviewer', label: '复核业务规则' },
  { value: 'case_summarizer', label: '生成案例摘要' },
  { value: 'translation_reviewer', label: '复核翻译' },
  { value: 'data_analyst', label: '补充数据分析' },
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
  const [testMessage, setTestMessage] = useState('')
  const [testKind, setTestKind] = useState<'playground' | 'replay'>('replay')
  const [specialists, setSpecialists] = useState<AgentSpecialist[]>([])
  const [generateReply, setGenerateReply] = useState(false)
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
  const testRun = useMutation({
    mutationFn: () => agentRuntimeApi.forkRun(selectedRunId as number, {
      tenant_key: tenantKey,
      environment: scope.environment,
      market_id: scope.market_id,
      channel: scope.channel || 'webchat',
      language: scope.language,
      case_type: scope.case_type,
      body: testMessage,
      fork_kind: testKind,
      cohort_key: `operator-${testKind}`,
      specialists,
      execute_model: generateReply,
    }),
  })
  useEffect(() => {
    testRun.reset()
    setTestMessage('')
    setSpecialists([])
    setGenerateReply(false)
  // Reset only when the selected authority changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId])

  const terminalRun = selectedRun && selectedRun.status !== 'running'
  const canRunTest = Boolean(terminalRun && testMessage.trim() && !testRun.isPending)

  return (
    <Paper component="section" variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <TimelineRoundedIcon color="primary" aria-hidden="true" />
          <Typography component="h2" variant="h2">运行记录</Typography>
        </Stack>
        {runs.isFetching ? <CircularProgress size={18} aria-label="正在刷新" /> : null}
      </Stack>
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
          <OperatorErrorNotice title="无法读取运行记录" error={runs.error} fallback="请稍后重试" />
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
        <Paper component="aside" variant="outlined" sx={{ p: 1, maxHeight: 760, overflow: 'auto' }}>
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
                    <Typography variant="subtitle2">记录 #{run.id}</Typography>
                    <Chip size="small" color={runColor(run.status)} label={runStatusLabel(run.status)} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                    用时 {run.elapsed_ms} ms
                  </Typography>
                  {run.parent_run_id ? (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      基于记录 #{run.parent_run_id} 创建
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
            <OperatorEmptyState title="暂无运行记录" description="自动处理执行后会在此生成记录" />
          )}
        </Paper>

        <Box sx={{ minWidth: 0 }}>
          {!selectedRun ? (
            <OperatorEmptyState title="请选择一条记录" description="查看处理过程或基于记录进行测试" />
          ) : events.error ? (
            <OperatorErrorNotice title="无法读取处理过程" error={events.error} fallback="请稍后重试" />
          ) : events.isLoading || !events.data ? (
            <Stack sx={{ minHeight: 240, alignItems: 'center', justifyContent: 'center' }}>
              <CircularProgress size={28} />
            </Stack>
          ) : (
            <Stack spacing={1.25}>
              <Alert severity={selectedRun.status === 'failed' ? 'error' : selectedRun.status === 'fallback' ? 'warning' : 'info'}>
                {runStatusLabel(selectedRun.status)} · {events.data.events.length} 个处理步骤 · 最终结果 {selectedRun.final_action || '未完成'}
              </Alert>

              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <ReplayRoundedIcon color="primary" aria-hidden="true" />
                  <Typography variant="h3">基于此记录测试</Typography>
                </Stack>
                {!terminalRun ? (
                  <Alert severity="info" sx={{ mt: 1.5 }}>处理中记录暂不能用于测试。</Alert>
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
                    label="测试方式"
                    value={testKind}
                    onChange={(event) => setTestKind(event.target.value as 'playground' | 'replay')}
                    disabled={!terminalRun}
                  >
                    <MenuItem value="replay">复用原配置</MenuItem>
                    <MenuItem value="playground">自定义测试</MenuItem>
                  </TextField>
                  <TextField
                    label="测试消息"
                    value={testMessage}
                    onChange={(event) => setTestMessage(event.target.value)}
                    multiline
                    minRows={3}
                    disabled={!terminalRun}
                  />
                </Box>
                <Typography variant="subtitle2" sx={{ mt: 1.5 }}>附加检查（最多三项）</Typography>
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
                      checked={generateReply}
                      disabled={!terminalRun || !canExecute}
                      onChange={(event) => setGenerateReply(event.target.checked)}
                    />
                  )}
                  label={canExecute ? '生成测试回复' : '当前账号只能预览测试范围'}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 1 }}>
                  <Button
                    variant={generateReply ? 'contained' : 'outlined'}
                    startIcon={generateReply ? <PlayArrowRoundedIcon /> : <ReplayRoundedIcon />}
                    disabled={!canRunTest}
                    onClick={() => testRun.mutate()}
                  >
                    {generateReply ? '开始测试' : '检查可用范围'}
                  </Button>
                  {testRun.isPending ? <CircularProgress size={24} /> : null}
                </Stack>
                {testRun.error ? (
                  <Box sx={{ mt: 1.5 }}>
                    <OperatorErrorNotice
                      title="无法开始测试"
                      error={testRun.error}
                      fallback="请检查当前生效版本是否与原记录一致"
                    />
                  </Box>
                ) : null}
                {testRun.data ? (
                  <Stack spacing={1} sx={{ mt: 1.5 }}>
                    <Alert severity={testRun.data.error_code ? 'warning' : 'success'}>
                      {testRun.data.model_executed
                        ? `测试完成${testRun.data.agent_run_id ? `，记录 #${testRun.data.agent_run_id}` : ''}`
                        : `范围检查完成，可使用 ${testRun.data.read_tools.length} 个只读工具`}
                    </Alert>
                    {testRun.data.reply ? (
                      <Paper variant="outlined" sx={{ p: 1.25 }}>
                        <Typography variant="caption" color="text.secondary">测试回复</Typography>
                        <Typography sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>{testRun.data.reply}</Typography>
                      </Paper>
                    ) : null}
                    <OperatorTechnicalDisclosure title="测试详情">
                      <Box component="pre" sx={{ m: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                        {JSON.stringify(testRun.data, null, 2)}
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
                      <Typography variant="subtitle2">{eventTypeLabel(event.event_type)}</Typography>
                      <Chip size="small" variant="outlined" label={eventStatusLabel(event.status)} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      {event.duration_ms} ms · {new Date(event.created_at).toLocaleString()}
                    </Typography>
                  </Stack>
                  {Object.keys(event.safe_payload || {}).length ? (
                    <OperatorTechnicalDisclosure title="步骤详情" summary={event.event_type} compact>
                      <Box component="pre" sx={{ m: 0, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                        {JSON.stringify(event.safe_payload, null, 2)}
                      </Box>
                    </OperatorTechnicalDisclosure>
                  ) : null}
                </Paper>
              ))}
              <OperatorTechnicalDisclosure title="运行详情" summary="版本、范围和追踪信息">
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

function runColor(status: AgentRun['status']): 'success' | 'warning' | 'error' | 'info' | 'default' {
  if (status === 'succeeded') return 'success'
  if (status === 'fallback') return 'warning'
  if (status === 'failed') return 'error'
  if (status === 'running') return 'info'
  return 'default'
}

function runStatusLabel(status: AgentRun['status']) {
  return STATUS_OPTIONS.find((item) => item.value === status)?.label || status
}

function eventStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '处理中',
    succeeded: '已完成',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过',
    cancelled: '已取消',
  }
  return labels[status] || status
}

function eventTypeLabel(value: string) {
  const labels: Record<string, string> = {
    run_started: '开始处理',
    context_compiled: '准备处理信息',
    model_started: '开始生成',
    model_completed: '生成完成',
    tool_requested: '请求工具',
    tool_completed: '工具完成',
    specialist_requested: '请求附加检查',
    specialist_completed: '附加检查完成',
    run_completed: '处理完成',
    run_failed: '处理失败',
  }
  return labels[value] || value.replaceAll('_', ' ')
}

function toggleSpecialist(
  current: AgentSpecialist[],
  specialist: AgentSpecialist,
  checked: boolean,
) {
  if (checked) return current.includes(specialist) ? current : [...current, specialist].slice(0, 3)
  return current.filter((item) => item !== specialist)
}
