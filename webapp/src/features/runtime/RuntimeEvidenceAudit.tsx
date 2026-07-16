import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded'
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Alert,
  AlertTitle,
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
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { aiDebugApi, type AiDebugBundle, type AiDebugRun } from './aiDebugApi'

type BoolFilter = 'all' | 'true' | 'false'
type Tone = 'default' | 'warning' | 'success' | 'danger'

const findingTypes = [
  ['irrelevant_answer', '回答不相关'],
  ['answered_live_tracking_without_tool_fact', '无工具事实却回答实时物流'],
  ['used_kb_for_live_tracking', '用知识库回答实时物流'],
  ['used_previous_ai_reply_as_fact', '用历史 AI 回复当事实'],
  ['used_customer_claim_as_fact', '用客户说法当事实'],
  ['tool_fact_ignored', '工具事实未被使用'],
  ['should_handoff_but_did_not', '应转人工但未转'],
  ['should_clarify_but_did_not', '应追问但未追问'],
  ['safety_should_block', 'Safety 应拦未拦'],
  ['safety_false_block', 'Safety 误拦'],
  ['knowledge_miss', '知识库未命中'],
  ['tool_error', '工具错误'],
  ['other', '其它'],
] as const

function filterBool(value: BoolFilter) {
  if (value === 'true') return true
  if (value === 'false') return false
  return undefined
}

function runTone(run?: AiDebugRun | null): Tone {
  if (!run) return 'default'
  if (run.status === 'failed' || run.status === 'timeout') return 'danger'
  if (run.tracking_intent_detected && !run.tracking_fact_evidence_present && run.live_tracking_answer_allowed) return 'danger'
  if (run.status === 'completed' || run.customer_visible_message_created) return 'success'
  if (run.status === 'processing' || run.status === 'bridge_calling' || run.status === 'queued') return 'warning'
  return 'default'
}

function statusColor(tone: Tone) {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'error'
  return 'default'
}

function errorCopy(error: unknown) {
  return error instanceof Error && error.message ? error.message : '请稍后重试'
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <Stack role="status" alignItems="center" justifyContent="center" spacing={0.75} sx={{ minHeight: 130, p: 3, textAlign: 'center' }}>
      <Typography variant="subtitle2">{title}</Typography>
      <Typography variant="body2" color="text.secondary">{description}</Typography>
    </Stack>
  )
}

function FactGrid({ facts }: { facts: Array<[string, React.ReactNode]> }) {
  return (
    <Box component="dl" sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, minmax(0, 1fr))' }, m: 0 }}>
      {facts.map(([label, value]) => (
        <Box key={label} sx={{ minWidth: 0 }}>
          <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
          <Typography component="dd" variant="body2" sx={{ m: 0, mt: 0.5, overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}>{value}</Typography>
        </Box>
      ))}
    </Box>
  )
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
  const [lastFinding, setLastFinding] = useState<Record<string, any> | null>(null)
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
  const timeline = Array.isArray(bundle?.timeline) ? bundle.timeline : []
  const toolCalls = Array.isArray(bundle?.tool_calls) ? bundle.tool_calls : []

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
    mutationFn: () => aiDebugApi.createEvalCase(Number(lastFinding?.id)),
  })

  const copyBundle = async () => {
    if (!bundle) return
    await navigator.clipboard.writeText(JSON.stringify(bundle, null, 2))
  }

  return (
    <Box component="section" aria-labelledby="runtime-audit-title" data-testid="runtime-evidence-audit">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'flex-start' }} justifyContent="space-between">
        <Box>
          <Typography id="runtime-audit-title" component="h2" variant="h2">证据审计</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>检查回复证据链、工具调用、知识命中和安全结果，并把确认问题保存为回归案例。</Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button color="inherit" variant="outlined" startIcon={<RefreshRoundedIcon />} onClick={() => runsQuery.refetch()}>刷新</Button>
          <Button color="inherit" variant="outlined" startIcon={<ContentCopyRoundedIcon />} disabled={!bundle} onClick={copyBundle}>复制脱敏证据包</Button>
        </Stack>
      </Stack>

      <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
        <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(5, 1fr)' } }}>
          <TextField select label="时间范围" value={String(sinceHours)} onChange={(event) => setSinceHours(Number(event.target.value))}>
            <MenuItem value="6">最近 6 小时</MenuItem><MenuItem value="24">最近 24 小时</MenuItem><MenuItem value="72">最近 3 天</MenuItem><MenuItem value="168">最近 7 天</MenuItem>
          </TextField>
          <TextField label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)} placeholder="留空表示全部" />
          <TextField select label="工具事实" value={trackingEvidence} onChange={(event) => setTrackingEvidence(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">有</MenuItem><MenuItem value="false">无</MenuItem>
          </TextField>
          <TextField select label="实时物流回答" value={liveAllowed} onChange={(event) => setLiveAllowed(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">允许</MenuItem><MenuItem value="false">禁止</MenuItem>
          </TextField>
          <TextField select label="客户可见消息" value={visibleCreated} onChange={(event) => setVisibleCreated(event.target.value as BoolFilter)}>
            <MenuItem value="all">全部</MenuItem><MenuItem value="true">已创建</MenuItem><MenuItem value="false">未创建</MenuItem>
          </TextField>
        </Box>
      </Paper>

      {runsQuery.isError ? <Alert severity="error" variant="outlined" sx={{ mt: 2 }}><AlertTitle>证据审计数据不可用</AlertTitle>{errorCopy(runsQuery.error)}</Alert> : null}

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(260px, 340px) minmax(0, 1fr)' }, mt: 2 }}>
        <Paper component="aside" variant="outlined" aria-label="AI 运行记录" sx={{ alignSelf: 'start', minWidth: 0, p: 1.5, position: { lg: 'sticky' }, top: { lg: 84 } }}>
          <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
            <Typography component="h3" variant="h3">运行记录</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>{runs.length}</Typography>
          </Stack>
          <Divider sx={{ mt: 1.5 }} />
          {!runs.length ? <EmptyState title="暂无审计记录" description="产生自动处理记录后会自动显示。" /> : (
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
                      <Typography variant="subtitle2">Turn #{run.ai_turn_id}</Typography>
                      <Chip color={statusColor(runTone(run))} label={sanitizeDisplayText(run.status)} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary">Ticket #{run.ticket_id} · {sanitizeDisplayText(run.channel || '未知渠道')}</Typography>
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
              <Typography component="h3" variant="h3">本轮结论</Typography>
              {selectedRun ? <Chip color={statusColor(runTone(selectedRun))} label={sanitizeDisplayText(selectedRun.status)} /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            <FactGrid facts={[
              ['意图', sanitizeDisplayText(selectedRun?.intent || bundle?.summary?.intent || '未知')],
              ['工具事实', selectedRun?.tracking_fact_evidence_present ? '有' : '无'],
              ['实时物流回答', selectedRun?.live_tracking_answer_allowed ? '允许' : '禁止'],
              ['知识命中', selectedRun?.kb_hits_count ?? bundle?.evidence?.kb_hits_count ?? 0],
              ['客户可见消息', selectedRun?.customer_visible_message_created ? '已创建' : '未创建'],
            ]} />
          </Paper>

          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: '1fr 1fr' } }}>
            <Paper component="section" variant="outlined" sx={{ p: 2 }}>
              <Typography component="h3" variant="h3">工具调用</Typography>
              <Divider sx={{ my: 2 }} />
              {!toolCalls.length ? <EmptyState title="暂无工具调用" description="本轮没有关联工具调用，或仍在处理中。" /> : (
                <Stack divider={<Divider flexItem />}>
                  {toolCalls.map((call: any) => (
                    <Box component="article" key={`${call.id}-${call.tool_name}`} sx={{ py: 1.25 }}>
                      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                        <Typography variant="subtitle2">{sanitizeDisplayText(call.tool_name || '未知工具')}</Typography>
                        <Chip color={call.status === 'success' ? 'success' : call.status === 'failed' ? 'error' : 'warning'} label={sanitizeDisplayText(call.status || 'unknown')} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(call.provider || 'provider')} · {typeof call.elapsed_ms === 'number' ? `${call.elapsed_ms}ms` : '暂无耗时'} · {call.redaction_applied ? '已脱敏' : '脱敏状态未知'}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Paper>

            <Paper component="section" variant="outlined" sx={{ p: 2 }}>
              <Typography component="h3" variant="h3">事件时间线</Typography>
              <Divider sx={{ my: 2 }} />
              {!timeline.length ? <EmptyState title="暂无事件时间线" description="选择一条记录后显示事件回放。" /> : (
                <Stack divider={<Divider flexItem />}>
                  {timeline.map((item: any) => (
                    <Box component="article" key={`${item.event_id}-${item.event_type}`} sx={{ py: 1.25 }}>
                      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                        <Typography variant="subtitle2">{sanitizeDisplayText(item.event_type || item.phase || 'event')}</Typography>
                        <Chip label={sanitizeDisplayText(item.status || item.phase || 'event')} />
                      </Stack>
                      <Typography variant="caption" color="text.disabled">{item.created_at ? formatDateTime(item.created_at) : ''}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Paper>
          </Box>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h3" variant="h3">记录测试问题</Typography>
              {lastFinding ? <Chip color="success" label={`Finding #${lastFinding.id}`} /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            <Stack spacing={1.5}>
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField select label="问题类型" value={findingType} onChange={(event) => setFindingType(event.target.value)}>
                  {findingTypes.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                </TextField>
                <TextField select label="严重级别" value={severity} onChange={(event) => setSeverity(event.target.value)}>
                  <MenuItem value="low">低</MenuItem><MenuItem value="medium">中</MenuItem><MenuItem value="high">高</MenuItem><MenuItem value="critical">严重</MenuItem>
                </TextField>
              </Box>
              <TextField label="测试说明" value={testerNote} onChange={(event) => setTesterNote(event.target.value)} multiline minRows={3} />
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField label="期望行为" value={expectedBehavior} onChange={(event) => setExpectedBehavior(event.target.value)} multiline minRows={2} />
                <TextField label="实际行为" value={actualBehavior} onChange={(event) => setActualBehavior(event.target.value)} multiline minRows={2} />
              </Box>
              {findingMutation.isError ? <Alert severity="error" variant="outlined"><AlertTitle>保存测试问题失败</AlertTitle>{errorCopy(findingMutation.error)}</Alert> : null}
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="contained" disabled={!selectedRun || findingMutation.isPending} startIcon={findingMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => findingMutation.mutate()}>
                  {findingMutation.isPending ? '保存中…' : '保存测试问题'}
                </Button>
                {lastFinding ? <Button variant="outlined" color="inherit" disabled={evalMutation.isPending} onClick={() => evalMutation.mutate()}>{evalMutation.isPending ? '生成中…' : '保存为回归案例'}</Button> : null}
              </Stack>
            </Stack>
          </Paper>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h3" variant="h3">脱敏证据包</Typography>
              <Button color="inherit" onClick={() => setShowJson((value) => !value)}>{showJson ? '收起 JSON' : '查看 JSON'}</Button>
            </Stack>
            {bundleQuery.isError ? <Alert severity="error" variant="outlined" sx={{ mt: 2 }}><AlertTitle>证据包不可用</AlertTitle>{errorCopy(bundleQuery.error)}</Alert> : null}
            <Collapse in={showJson} unmountOnExit>
              <Box component="pre" sx={{ bgcolor: 'text.primary', borderRadius: 1, color: 'background.paper', m: 0, mt: 2, maxHeight: 520, overflow: 'auto', p: 2, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {JSON.stringify(bundle, null, 2)}
              </Box>
            </Collapse>
            {!showJson ? <EmptyState title="证据包默认收起" description="仅在审计或故障排查时展开。" /> : null}
          </Paper>
        </Stack>
      </Box>
    </Box>
  )
}
