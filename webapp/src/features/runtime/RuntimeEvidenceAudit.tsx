import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded'
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import type { OperatorTone } from '@/app/OperatorPresentation'
import {
  finiteNumber,
  formatDateTime,
  recordArrayValue,
  recordValue,
  sanitizeDisplayText,
  stringValue,
} from '@/lib/format'
import { aiDebugApi, type AiDebugBundle, type AiDebugFinding, type AiDebugRun } from './aiDebugApi'

type BoolFilter = 'all' | 'true' | 'false'

const findingTypes = [
  ['irrelevant_answer', '回复不相关'],
  ['answered_live_tracking_without_tool_fact', '没有查询结果却回复实时物流'],
  ['used_kb_for_live_tracking', '使用知识回答实时物流'],
  ['used_previous_ai_reply_as_fact', '把历史自动回复当成事实'],
  ['used_customer_claim_as_fact', '把客户说法当成事实'],
  ['tool_fact_ignored', '未使用查询结果'],
  ['should_handoff_but_did_not', '应转人工但未转'],
  ['should_clarify_but_did_not', '应追问但未追问'],
  ['safety_should_block', '安全检查应拦截但未拦截'],
  ['safety_false_block', '安全检查误拦截'],
  ['knowledge_miss', '知识未匹配'],
  ['tool_error', '查询或操作失败'],
  ['other', '其他'],
] as const

function filterBool(value: BoolFilter) {
  if (value === 'true') return true
  if (value === 'false') return false
  return undefined
}

function runTone(run?: AiDebugRun | null): OperatorTone {
  if (!run) return 'default'
  if (run.status === 'failed' || run.status === 'timeout') return 'danger'
  if (run.tracking_intent_detected && !run.tracking_fact_evidence_present && run.live_tracking_answer_allowed) return 'danger'
  if (run.status === 'completed' || run.customer_visible_message_created) return 'success'
  if (run.status === 'processing' || run.status === 'bridge_calling' || run.status === 'queued') return 'warning'
  return 'default'
}

export function RuntimeEvidenceAudit() {
  const [sinceHours, setSinceHours] = useState(24)
  const [channel, setChannel] = useState('')
  const [trackingEvidence, setTrackingEvidence] = useState<BoolFilter>('all')
  const [liveAllowed, setLiveAllowed] = useState<BoolFilter>('all')
  const [visibleCreated, setVisibleCreated] = useState<BoolFilter>('all')
  const [selectedTurnId, setSelectedTurnId] = useState<number | null>(null)
  const [findingType, setFindingType] = useState('answered_live_tracking_without_tool_fact')
  const [severity, setSeverity] = useState('high')
  const [testerNote, setTesterNote] = useState('')
  const [expectedBehavior, setExpectedBehavior] = useState('')
  const [actualBehavior, setActualBehavior] = useState('')
  const [lastFinding, setLastFinding] = useState<AiDebugFinding | null>(null)
  const [showJson, setShowJson] = useState(false)

  const runsQuery = useQuery({
    queryKey: ['runtimeEvidenceAuditRuns', sinceHours, channel, trackingEvidence, liveAllowed, visibleCreated],
    queryFn: () => aiDebugApi.listDebugRuns({
      since_hours: sinceHours,
      channel: channel.trim() || undefined,
      tracking_fact_evidence_present: filterBool(trackingEvidence),
      live_tracking_answer_allowed: filterBool(liveAllowed),
      customer_visible_message_created: filterBool(visibleCreated),
      limit: 80,
    }),
    refetchInterval: 5000,
    retry: false,
  })
  const runs = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data?.items])
  const selectedRun = useMemo(
    () => runs.find((item) => item.ai_turn_id === selectedTurnId) ?? runs[0] ?? null,
    [runs, selectedTurnId],
  )
  const bundleQuery = useQuery({
    queryKey: ['runtimeEvidenceAuditBundle', selectedRun?.ai_turn_id],
    queryFn: () => aiDebugApi.getDebugBundle(selectedRun?.ai_turn_id ?? 0),
    enabled: Boolean(selectedRun?.ai_turn_id),
    refetchInterval: 5000,
    retry: false,
  })
  const bundle: AiDebugBundle | null = bundleQuery.data ?? null
  const summary = recordValue(bundle?.summary)
  const evidence = recordValue(bundle?.evidence)
  const timeline = recordArrayValue(bundle?.timeline)
  const toolCalls = recordArrayValue(bundle?.tool_calls)

  const findingMutation = useMutation({
    mutationFn: () => aiDebugApi.createFinding(selectedRun?.ai_turn_id ?? 0, {
      finding_type: findingType,
      severity,
      tester_note: testerNote.trim() || null,
      expected_behavior: expectedBehavior.trim() || null,
      actual_behavior: actualBehavior.trim() || null,
    }),
    onSuccess: setLastFinding,
  })
  const evalMutation = useMutation({
    mutationFn: () => aiDebugApi.createEvalCase(finiteNumber(lastFinding?.id, 0)),
  })

  const copyBundle = async () => {
    if (!bundle) return
    await navigator.clipboard.writeText(JSON.stringify(bundle, null, 2))
  }

  return (
    <Box component="section" aria-labelledby="runtime-audit-title" data-testid="runtime-evidence-audit">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'flex-start' }} justifyContent="space-between">
        <Typography id="runtime-audit-title" component="h2" variant="h2">证据审计</Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button color="inherit" variant="outlined" startIcon={<RefreshRoundedIcon />} onClick={() => runsQuery.refetch()}>刷新</Button>
          <Button color="inherit" variant="outlined" startIcon={<ContentCopyRoundedIcon />} disabled={!bundle} onClick={copyBundle}>复制审计数据</Button>
        </Stack>
      </Stack>

      <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
        <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(5, 1fr)' } }}>
          <TextField select label="时间范围" value={String(sinceHours)} onChange={(event) => setSinceHours(Number(event.target.value))}>
            <MenuItem value="6">最近 6 小时</MenuItem><MenuItem value="24">最近 24 小时</MenuItem><MenuItem value="72">最近 3 天</MenuItem><MenuItem value="168">最近 7 天</MenuItem>
          </TextField>
          <TextField label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)} placeholder="全部" />
          <TextField select label="查询结果" value={trackingEvidence} onChange={(event) => setTrackingEvidence(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">有</MenuItem><MenuItem value="false">无</MenuItem>
          </TextField>
          <TextField select label="实时物流回复" value={liveAllowed} onChange={(event) => setLiveAllowed(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">允许</MenuItem><MenuItem value="false">禁止</MenuItem>
          </TextField>
          <TextField select label="客户消息" value={visibleCreated} onChange={(event) => setVisibleCreated(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">已创建</MenuItem><MenuItem value="false">未创建</MenuItem>
          </TextField>
        </Box>
      </Paper>

      {runsQuery.isError ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取审计数据" error={runsQuery.error} fallback="请稍后重试" /></Box> : null}

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(260px, 340px) minmax(0, 1fr)' }, mt: 2 }}>
        <Paper component="aside" variant="outlined" aria-label="处理记录" sx={{ alignSelf: 'start', minWidth: 0, p: 1.5, position: { lg: 'sticky' }, top: { lg: 84 } }}>
          <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
            <Typography component="h3" variant="h3">处理记录</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>{runs.length}</Typography>
          </Stack>
          <Divider sx={{ mt: 1.5 }} />
          {!runs.length ? <OperatorEmptyState title="暂无记录" description="暂无数据" /> : (
            <List disablePadding sx={{ maxHeight: { lg: 'calc(100dvh - 300px)' }, overflowY: 'auto' }}>
              {runs.map((run) => (
                <ListItemButton
                  component="button"
                  key={run.id}
                  selected={selectedRun?.ai_turn_id === run.ai_turn_id}
                  onClick={() => { setSelectedTurnId(run.ai_turn_id); setLastFinding(null) }}
                  sx={{ borderBottom: 1, borderColor: 'divider', display: 'block', px: 1.25, py: 1.25, textAlign: 'left', width: '100%' }}
                >
                  <Stack spacing={0.75}>
                    <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                      <Typography variant="subtitle2">处理 #{run.ai_turn_id}</Typography>
                      <Chip color={operatorToneColor(runTone(run))} label={sanitizeDisplayText(run.status)} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary">工单 #{run.ticket_id} · {sanitizeDisplayText(run.channel || '未知渠道')}</Typography>
                    <Typography variant="caption" color="text.disabled">{run.created_at ? formatDateTime(run.created_at) : '暂无时间'}</Typography>
                  </Stack>
                </ListItemButton>
              ))}
            </List>
          )}
        </Paper>

        <Stack spacing={2} sx={{ minWidth: 0 }}>
          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h3" variant="h3">处理结果</Typography>
              {selectedRun ? <Chip color={operatorToneColor(runTone(selectedRun))} label={sanitizeDisplayText(selectedRun.status)} /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            <OperatorFactGrid columns={5} facts={[
              ['问题类型', sanitizeDisplayText(selectedRun?.intent || stringValue(summary.intent, '未知'))],
              ['查询结果', selectedRun?.tracking_fact_evidence_present ? '有' : '无'],
              ['实时物流回复', selectedRun?.live_tracking_answer_allowed ? '允许' : '禁止'],
              ['知识匹配', selectedRun?.kb_hits_count ?? finiteNumber(evidence.kb_hits_count, 0)],
              ['客户消息', selectedRun?.customer_visible_message_created ? '已创建' : '未创建'],
            ]} />
          </Paper>

          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: '1fr 1fr' } }}>
            <Paper component="section" variant="outlined" sx={{ p: 2 }}>
              <Typography component="h3" variant="h3">查询与操作记录</Typography>
              <Divider sx={{ my: 2 }} />
              {!toolCalls.length ? <OperatorEmptyState title="暂无查询或操作" description="暂无数据" /> : (
                <Stack divider={<Divider flexItem />}>
                  {toolCalls.map((call, index) => {
                    const status = stringValue(call.status, 'unknown')
                    const color = status === 'success' ? 'success' : status === 'failed' ? 'error' : 'warning'
                    return (
                      <Box component="article" key={`${stringValue(call.id, String(index))}-${stringValue(call.tool_name)}`} sx={{ py: 1.25 }}>
                        <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                          <Typography variant="subtitle2">{sanitizeDisplayText(stringValue(call.tool_name, '未知操作'))}</Typography>
                          <Chip color={color} label={sanitizeDisplayText(status)} />
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {sanitizeDisplayText(stringValue(call.provider, '未知服务'))} · {typeof call.elapsed_ms === 'number' ? `${call.elapsed_ms}ms` : '暂无耗时'} · {call.redaction_applied === true ? '已脱敏' : '脱敏状态未知'}
                        </Typography>
                      </Box>
                    )
                  })}
                </Stack>
              )}
            </Paper>

            <Paper component="section" variant="outlined" sx={{ p: 2 }}>
              <Typography component="h3" variant="h3">处理时间线</Typography>
              <Divider sx={{ my: 2 }} />
              {!timeline.length ? <OperatorEmptyState title="暂无时间线" description="暂无数据" /> : (
                <Stack divider={<Divider flexItem />}>
                  {timeline.map((item, index) => (
                    <Box component="article" key={`${stringValue(item.event_id, String(index))}-${stringValue(item.event_type)}`} sx={{ py: 1.25 }}>
                      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                        <Typography variant="subtitle2">{sanitizeDisplayText(stringValue(item.event_type, stringValue(item.phase, '事件')))}</Typography>
                        <Chip label={sanitizeDisplayText(stringValue(item.status, stringValue(item.phase, '事件')))} />
                      </Stack>
                      <Typography variant="caption" color="text.disabled">{stringValue(item.created_at) ? formatDateTime(stringValue(item.created_at)) : ''}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Paper>
          </Box>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h3" variant="h3">记录问题</Typography>
              {lastFinding?.id ? <Chip color="success" label={`问题记录 #${lastFinding.id}`} /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            <Stack spacing={1.5}>
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField select label="问题类型" value={findingType} onChange={(event) => setFindingType(event.target.value)}>
                  {findingTypes.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                </TextField>
                <TextField select label="严重程度" value={severity} onChange={(event) => setSeverity(event.target.value)}>
                  <MenuItem value="low">低</MenuItem><MenuItem value="medium">中</MenuItem><MenuItem value="high">高</MenuItem><MenuItem value="critical">严重</MenuItem>
                </TextField>
              </Box>
              <TextField label="问题说明" value={testerNote} onChange={(event) => setTesterNote(event.target.value)} multiline minRows={3} />
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField label="期望结果" value={expectedBehavior} onChange={(event) => setExpectedBehavior(event.target.value)} multiline minRows={2} />
                <TextField label="实际结果" value={actualBehavior} onChange={(event) => setActualBehavior(event.target.value)} multiline minRows={2} />
              </Box>
              {findingMutation.isError ? <OperatorErrorNotice title="保存失败" error={findingMutation.error} fallback="请稍后重试" /> : null}
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="contained" disabled={!selectedRun || findingMutation.isPending} startIcon={findingMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => findingMutation.mutate()}>
                  {findingMutation.isPending ? '保存中…' : '保存问题'}
                </Button>
                {lastFinding?.id ? <Button variant="outlined" color="inherit" disabled={evalMutation.isPending} onClick={() => evalMutation.mutate()}>{evalMutation.isPending ? '生成中…' : '加入回归测试'}</Button> : null}
              </Stack>
            </Stack>
          </Paper>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h3" variant="h3">审计数据</Typography>
              <Button color="inherit" onClick={() => setShowJson((value) => !value)}>{showJson ? '收起原始数据' : '查看原始数据'}</Button>
            </Stack>
            {bundleQuery.isError ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取审计数据" error={bundleQuery.error} fallback="请稍后重试" /></Box> : null}
            <Collapse in={showJson} unmountOnExit>
              <Box component="pre" sx={{ bgcolor: 'text.primary', borderRadius: 1, color: 'background.paper', m: 0, mt: 2, maxHeight: 520, overflow: 'auto', p: 2, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {JSON.stringify(bundle, null, 2)}
              </Box>
            </Collapse>
            {!showJson ? <OperatorEmptyState title="原始数据已收起" description="按需查看" /> : null}
          </Paper>
        </Stack>
      </Box>
    </Box>
  )
}
