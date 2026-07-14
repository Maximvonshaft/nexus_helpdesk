import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { aiDebugApi, type AiDebugBundle, type AiDebugRun } from './aiDebugApi'
import './runtime-evidence-audit.css'

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

function errorCopy(error: unknown) {
  return error instanceof Error && error.message ? error.message : '请稍后重试'
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
    <section className="runtime-audit" aria-labelledby="runtime-audit-title" data-testid="runtime-evidence-audit">
      <header className="runtime-audit__header">
        <div>
          <h2 id="runtime-audit-title">AI 证据审计</h2>
          <p>检查回复证据链、工具调用、知识命中和安全结果，并把确认问题保存为回归案例。</p>
        </div>
        <div className="runtime-audit__actions">
          <Button variant="secondary" onClick={() => runsQuery.refetch()}>刷新</Button>
          <Button variant="secondary" disabled={!bundle} onClick={copyBundle}>复制脱敏证据包</Button>
        </div>
      </header>

      <div className="runtime-audit__filters">
        <Field label="时间范围"><Select value={String(sinceHours)} onChange={(event) => setSinceHours(Number(event.target.value))}><option value="6">最近 6 小时</option><option value="24">最近 24 小时</option><option value="72">最近 3 天</option><option value="168">最近 7 天</option></Select></Field>
        <Field label="渠道"><Input value={channel} onChange={(event) => setChannel(event.target.value)} placeholder="留空表示全部" /></Field>
        <Field label="工具事实"><Select value={trackingEvidence} onChange={(event) => setTrackingEvidence(event.target.value as BoolFilter)}><option value="all">全部</option><option value="true">有</option><option value="false">无</option></Select></Field>
        <Field label="实时物流回答"><Select value={liveAllowed} onChange={(event) => setLiveAllowed(event.target.value as BoolFilter)}><option value="all">全部</option><option value="true">允许</option><option value="false">禁止</option></Select></Field>
        <Field label="客户可见消息"><Select value={visibleCreated} onChange={(event) => setVisibleCreated(event.target.value as BoolFilter)}><option value="all">全部</option><option value="true">已创建</option><option value="false">未创建</option></Select></Field>
      </div>

      {runsQuery.isError ? <ErrorSummary title="证据审计数据不可用" errors={[errorCopy(runsQuery.error)]} /> : null}

      <div className="runtime-audit__layout">
        <aside className="runtime-audit__runs" aria-label="AI 运行记录">
          <div className="runtime-audit__panel-head"><strong>运行记录</strong><Badge>{runs.length}</Badge></div>
          {!runs.length ? <EmptyState title="暂无审计记录" description="产生 AI 处理记录后会自动显示。" /> : runs.map((run) => (
            <button type="button" key={run.id} className={selectedRun?.ai_turn_id === run.ai_turn_id ? 'is-active' : ''} onClick={() => { setSelectedTurnId(run.ai_turn_id); setLastFinding(null) }}>
              <span><strong>Turn #{run.ai_turn_id}</strong><Badge tone={runTone(run)}>{sanitizeDisplayText(run.status)}</Badge></span>
              <small>Ticket #{run.ticket_id} · {sanitizeDisplayText(run.channel || '未知渠道')}</small>
              <small>{run.created_at ? formatDateTime(run.created_at) : '暂无时间'}</small>
            </button>
          ))}
        </aside>

        <div className="runtime-audit__detail">
          <section className="runtime-audit__panel">
            <div className="runtime-audit__panel-head"><strong>本轮结论</strong>{selectedRun ? <Badge tone={runTone(selectedRun)}>{sanitizeDisplayText(selectedRun.status)}</Badge> : null}</div>
            <dl className="runtime-audit__facts">
              <div><dt>意图</dt><dd>{sanitizeDisplayText(selectedRun?.intent || bundle?.summary?.intent || '未知')}</dd></div>
              <div><dt>工具事实</dt><dd>{selectedRun?.tracking_fact_evidence_present ? '有' : '无'}</dd></div>
              <div><dt>实时物流回答</dt><dd>{selectedRun?.live_tracking_answer_allowed ? '允许' : '禁止'}</dd></div>
              <div><dt>知识命中</dt><dd>{selectedRun?.kb_hits_count ?? bundle?.evidence?.kb_hits_count ?? 0}</dd></div>
              <div><dt>客户可见消息</dt><dd>{selectedRun?.customer_visible_message_created ? '已创建' : '未创建'}</dd></div>
            </dl>
          </section>

          <div className="runtime-audit__two-col">
            <section className="runtime-audit__panel">
              <div className="runtime-audit__panel-head"><strong>工具调用</strong></div>
              {!toolCalls.length ? <EmptyState title="暂无工具调用" description="本轮没有关联工具调用，或仍在处理中。" /> : toolCalls.map((call: any) => (
                <article className="runtime-audit__item" key={`${call.id}-${call.tool_name}`}><span><strong>{sanitizeDisplayText(call.tool_name || '未知工具')}</strong><Badge tone={call.status === 'success' ? 'success' : call.status === 'failed' ? 'danger' : 'warning'}>{sanitizeDisplayText(call.status || 'unknown')}</Badge></span><small>{sanitizeDisplayText(call.provider || 'provider')} · {typeof call.elapsed_ms === 'number' ? `${call.elapsed_ms}ms` : '暂无耗时'} · {call.redaction_applied ? '已脱敏' : '脱敏状态未知'}</small></article>
              ))}
            </section>
            <section className="runtime-audit__panel">
              <div className="runtime-audit__panel-head"><strong>事件时间线</strong></div>
              {!timeline.length ? <EmptyState title="暂无事件时间线" description="选择一条记录后显示事件回放。" /> : timeline.map((item: any) => (
                <article className="runtime-audit__item" key={`${item.event_id}-${item.event_type}`}><span><strong>{sanitizeDisplayText(item.event_type || item.phase || 'event')}</strong><Badge>{sanitizeDisplayText(item.status || item.phase || 'event')}</Badge></span><small>{item.created_at ? formatDateTime(item.created_at) : ''}</small></article>
              ))}
            </section>
          </div>

          <section className="runtime-audit__panel">
            <div className="runtime-audit__panel-head"><strong>记录测试问题</strong>{lastFinding ? <Badge tone="success">Finding #{lastFinding.id}</Badge> : null}</div>
            <div className="runtime-audit__form-grid">
              <Field label="问题类型"><Select value={findingType} onChange={(event) => setFindingType(event.target.value)}>{findingTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
              <Field label="严重级别"><Select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">严重</option></Select></Field>
            </div>
            <Field label="测试说明"><Textarea rows={3} value={testerNote} onChange={(event) => setTesterNote(event.target.value)} /></Field>
            <div className="runtime-audit__form-grid"><Field label="期望行为"><Textarea rows={2} value={expectedBehavior} onChange={(event) => setExpectedBehavior(event.target.value)} /></Field><Field label="实际行为"><Textarea rows={2} value={actualBehavior} onChange={(event) => setActualBehavior(event.target.value)} /></Field></div>
            {findingMutation.isError ? <ErrorSummary title="保存测试问题失败" errors={[errorCopy(findingMutation.error)]} /> : null}
            <div className="runtime-audit__actions"><Button variant="primary" disabled={!selectedRun || findingMutation.isPending} onClick={() => findingMutation.mutate()}>{findingMutation.isPending ? '保存中…' : '保存测试问题'}</Button>{lastFinding ? <Button variant="secondary" disabled={evalMutation.isPending} onClick={() => evalMutation.mutate()}>{evalMutation.isPending ? '生成中…' : '保存为回归案例'}</Button> : null}</div>
          </section>

          <section className="runtime-audit__panel">
            <div className="runtime-audit__panel-head"><strong>脱敏证据包</strong><Button variant="ghost" onClick={() => setShowJson((value) => !value)}>{showJson ? '收起 JSON' : '查看 JSON'}</Button></div>
            {bundleQuery.isError ? <ErrorSummary title="证据包不可用" errors={[errorCopy(bundleQuery.error)]} /> : null}
            {showJson ? <pre className="runtime-audit__json">{JSON.stringify(bundle, null, 2)}</pre> : <EmptyState title="证据包默认收起" description="仅在审计或故障排查时展开。" />}
          </section>
        </div>
      </div>
    </section>
  )
}
