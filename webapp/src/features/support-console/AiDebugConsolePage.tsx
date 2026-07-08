import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { aiDebugApi, type AiDebugBundle, type AiDebugRun } from './aiDebugApi'
import './ai-debug-console.css'

type Tone = 'default' | 'warning' | 'success' | 'danger'
type BoolFilter = 'all' | 'true' | 'false'

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

function toneForRun(run?: AiDebugRun | null): Tone {
  if (!run) return 'default'
  if (run.status === 'failed' || run.status === 'timeout') return 'danger'
  if (run.tracking_intent_detected && !run.tracking_fact_evidence_present && run.live_tracking_answer_allowed) return 'danger'
  if (run.status === 'completed' || run.customer_visible_message_created) return 'success'
  if (run.status === 'processing' || run.status === 'bridge_calling' || run.status === 'queued') return 'warning'
  return 'default'
}

function boolText(value: boolean | undefined | null) {
  return value ? '是' : '否'
}

function boolTone(value: boolean | undefined | null, positive = true): Tone {
  if (value === undefined || value === null) return 'default'
  return value === positive ? 'success' : 'warning'
}

function filterBool(value: BoolFilter): boolean | undefined {
  if (value === 'true') return true
  if (value === 'false') return false
  return undefined
}

function prettyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function useCopy() {
  const [copied, setCopied] = useState(false)
  const copy = async (value: unknown) => {
    await navigator.clipboard.writeText(typeof value === 'string' ? value : prettyJson(value))
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }
  return { copied, copy }
}

function RunCard({ run, active, onSelect }: { run: AiDebugRun; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`ai-debug-run-card${active ? ' active' : ''}`} onClick={onSelect}>
      <span className="ai-debug-run-top">
        <strong>Turn #{run.ai_turn_id}</strong>
        <Badge tone={toneForRun(run)}>{sanitizeDisplayText(run.status || 'unknown')}</Badge>
      </span>
      <span className="ai-debug-run-sub">
        Ticket #{run.ticket_id} · {sanitizeDisplayText(run.channel || 'channel unknown')} · {run.created_at ? formatDateTime(run.created_at) : '暂无时间'}
      </span>
      <span className="ai-debug-run-badges">
        <Badge tone={run.tracking_intent_detected ? 'warning' : 'default'}>{run.tracking_intent_detected ? 'Tracking' : 'General'}</Badge>
        <Badge tone={boolTone(run.tracking_fact_evidence_present)}>{run.tracking_fact_evidence_present ? '有工具事实' : '无工具事实'}</Badge>
        <Badge tone={boolTone(run.customer_visible_message_created)}>{run.customer_visible_message_created ? '已发客户' : '未发客户'}</Badge>
      </span>
    </button>
  )
}

function SummaryTiles({ run, bundle }: { run?: AiDebugRun | null; bundle?: AiDebugBundle | null }) {
  const evidence = bundle?.evidence || {}
  const policy = bundle?.policy || {}
  return (
    <div className="ai-debug-tiles">
      <div>
        <span>本轮状态</span>
        <strong>{sanitizeDisplayText(run?.status || bundle?.summary?.status || '暂无')}</strong>
      </div>
      <div>
        <span>意图</span>
        <strong>{sanitizeDisplayText(run?.intent || bundle?.summary?.intent || 'unknown')}</strong>
      </div>
      <div>
        <span>工具事实</span>
        <strong>{boolText(run?.tracking_fact_evidence_present ?? evidence.tracking_fact_evidence_present)}</strong>
      </div>
      <div>
        <span>实时物流回答</span>
        <strong>{(run?.live_tracking_answer_allowed ?? policy.live_tracking_answer_allowed) ? '允许' : '禁止'}</strong>
      </div>
      <div>
        <span>KB 命中</span>
        <strong>{run?.kb_hits_count ?? evidence.kb_hits_count ?? 0}</strong>
      </div>
      <div>
        <span>客户可见</span>
        <strong>{boolText(run?.customer_visible_message_created ?? bundle?.visible_message?.created)}</strong>
      </div>
    </div>
  )
}

function Timeline({ bundle }: { bundle?: AiDebugBundle | null }) {
  const timeline = Array.isArray(bundle?.timeline) ? bundle?.timeline : []
  if (!timeline.length) return <EmptyState title="暂无事件时间线" description="选择一条 AI turn 后会显示 WebChat event replay。" />
  return (
    <div className="ai-debug-timeline">
      {timeline.map((item: any) => (
        <div className="ai-debug-timeline-row" key={`${item.event_id}-${item.event_type}`}>
          <div>
            <strong>{sanitizeDisplayText(item.event_type || item.phase || 'event')}</strong>
            <small>{item.created_at ? formatDateTime(item.created_at) : ''}</small>
          </div>
          <Badge tone={String(item.event_type || '').startsWith('ai_turn.failed') ? 'danger' : 'default'}>{sanitizeDisplayText(item.status || item.phase || 'event')}</Badge>
        </div>
      ))}
    </div>
  )
}

function ToolCalls({ bundle }: { bundle?: AiDebugBundle | null }) {
  const calls = Array.isArray(bundle?.tool_calls) ? bundle?.tool_calls : []
  if (!calls.length) return <EmptyState title="暂无工具调用" description="本轮没有关联到 ToolCallLog，或工具调用仍在处理中。" />
  return (
    <div className="ai-debug-tool-list">
      {calls.map((call: any) => (
        <div className="ai-debug-tool-card" key={`${call.id}-${call.tool_name}`}>
          <div className="ai-debug-tool-head">
            <strong>{sanitizeDisplayText(call.tool_name || 'unknown_tool')}</strong>
            <Badge tone={call.status === 'success' ? 'success' : call.status === 'failed' ? 'danger' : 'warning'}>{sanitizeDisplayText(call.status || 'unknown')}</Badge>
          </div>
          <div className="ai-debug-tool-meta">
            <span>{sanitizeDisplayText(call.provider || 'provider')}</span>
            <span>{sanitizeDisplayText(call.tool_type || 'tool')}</span>
            <span>{typeof call.elapsed_ms === 'number' ? `${call.elapsed_ms}ms` : '暂无耗时'}</span>
            <span>{call.redaction_applied ? '已脱敏' : '未确认脱敏'}</span>
          </div>
          {call.error_code ? <small className="ai-debug-danger">{sanitizeDisplayText(call.error_code)}</small> : null}
        </div>
      ))}
    </div>
  )
}

function FindingForm({ aiTurnId, onSaved }: { aiTurnId?: number | null; onSaved: (finding: Record<string, any>) => void }) {
  const [findingType, setFindingType] = useState('answered_live_tracking_without_tool_fact')
  const [severity, setSeverity] = useState('high')
  const [testerNote, setTesterNote] = useState('')
  const [expectedBehavior, setExpectedBehavior] = useState('')
  const [actualBehavior, setActualBehavior] = useState('')
  const mutation = useMutation({
    mutationFn: () => aiDebugApi.createFinding(aiTurnId ?? 0, {
      finding_type: findingType,
      severity,
      tester_note: testerNote || null,
      expected_behavior: expectedBehavior || null,
      actual_behavior: actualBehavior || null,
    }),
    onSuccess: onSaved,
  })
  return (
    <div className="ai-debug-finding-form">
      <div className="ai-debug-form-grid">
        <Field label="问题类型">
          <Select value={findingType} onChange={(event) => setFindingType(event.target.value)}>
            {findingTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
        </Field>
        <Field label="严重级别">
          <Select value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
            <option value="critical">严重</option>
          </Select>
        </Field>
      </div>
      <Field label="测试说明">
        <Textarea value={testerNote} onChange={(event) => setTesterNote(event.target.value)} rows={3} placeholder="例如：工具没有事实，但 AI 回答了实时物流状态。" />
      </Field>
      <div className="ai-debug-form-grid">
        <Field label="期望行为">
          <Textarea value={expectedBehavior} onChange={(event) => setExpectedBehavior(event.target.value)} rows={2} placeholder="应追问或转人工。" />
        </Field>
        <Field label="实际行为">
          <Textarea value={actualBehavior} onChange={(event) => setActualBehavior(event.target.value)} rows={2} placeholder="AI 回答了已签收。" />
        </Field>
      </div>
      {mutation.isError ? <ErrorSummary title="标记问题失败" errors={[mutation.error instanceof Error ? mutation.error.message : '请稍后重试']} /> : null}
      <Button variant="primary" disabled={!aiTurnId || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? '保存中…' : '标记问题'}</Button>
    </div>
  )
}

export function AiDebugConsolePage() {
  const [sinceHours, setSinceHours] = useState(24)
  const [channel, setChannel] = useState('')
  const [trackingEvidence, setTrackingEvidence] = useState<BoolFilter>('all')
  const [liveAllowed, setLiveAllowed] = useState<BoolFilter>('all')
  const [visibleCreated, setVisibleCreated] = useState<BoolFilter>('all')
  const [selectedTurnId, setSelectedTurnId] = useState<number | null>(null)
  const [lastFinding, setLastFinding] = useState<Record<string, any> | null>(null)
  const [showJson, setShowJson] = useState(false)
  const { copied, copy } = useCopy()

  const runsQuery = useQuery({
    queryKey: ['aiDebugRuns', sinceHours, channel, trackingEvidence, liveAllowed, visibleCreated],
    queryFn: () => aiDebugApi.listDebugRuns({
      since_hours: sinceHours,
      channel: channel || undefined,
      tracking_fact_evidence_present: filterBool(trackingEvidence),
      live_tracking_answer_allowed: filterBool(liveAllowed),
      customer_visible_message_created: filterBool(visibleCreated),
      limit: 80,
    }),
    refetchInterval: 5000,
    retry: false,
  })
  const runs = runsQuery.data?.items ?? []
  const selectedRun = useMemo(() => runs.find((item) => item.ai_turn_id === selectedTurnId) ?? runs[0] ?? null, [runs, selectedTurnId])
  const bundleQuery = useQuery({
    queryKey: ['aiDebugBundle', selectedRun?.ai_turn_id],
    queryFn: () => aiDebugApi.getDebugBundle(selectedRun?.ai_turn_id ?? 0),
    enabled: Boolean(selectedRun?.ai_turn_id),
    refetchInterval: 5000,
    retry: false,
  })
  const bundle = bundleQuery.data ?? null
  const evalMutation = useMutation({
    mutationFn: () => aiDebugApi.createEvalCase(Number(lastFinding?.id)),
  })

  return (
    <main className="ai-debug-page" data-testid="nexus-ai-debug-console">
      <header className="ai-debug-header">
        <div>
          <div className="ai-debug-eyebrow">Nexus QA Observability</div>
          <h1>测试观测控制台</h1>
          <p>实时查看 AI 回复证据链、Speedaf 工具调用、RAG 命中、Safety 结果，并沉淀测试问题。</p>
        </div>
        <div className="ai-debug-header-actions">
          <Button variant="secondary" onClick={() => runsQuery.refetch()}>刷新</Button>
          <Button variant="primary" disabled={!bundle} onClick={() => copy(bundle)}>{copied ? '已复制' : '复制 Debug Bundle'}</Button>
        </div>
      </header>

      <section className="ai-debug-filters" aria-label="测试观测筛选">
        <Field label="时间范围">
          <Select value={String(sinceHours)} onChange={(event) => setSinceHours(Number(event.target.value))}>
            <option value="6">最近 6 小时</option>
            <option value="24">最近 24 小时</option>
            <option value="72">最近 3 天</option>
            <option value="168">最近 7 天</option>
          </Select>
        </Field>
        <Field label="渠道">
          <Input value={channel} onChange={(event) => setChannel(event.target.value)} placeholder="webchat / whatsapp / 留空全部" />
        </Field>
        <Field label="工具事实">
          <Select value={trackingEvidence} onChange={(event) => setTrackingEvidence(event.target.value as BoolFilter)}>
            <option value="all">全部</option>
            <option value="true">有</option>
            <option value="false">无</option>
          </Select>
        </Field>
        <Field label="实时物流回答">
          <Select value={liveAllowed} onChange={(event) => setLiveAllowed(event.target.value as BoolFilter)}>
            <option value="all">全部</option>
            <option value="true">允许</option>
            <option value="false">禁止</option>
          </Select>
        </Field>
        <Field label="客户可见消息">
          <Select value={visibleCreated} onChange={(event) => setVisibleCreated(event.target.value as BoolFilter)}>
            <option value="all">全部</option>
            <option value="true">已创建</option>
            <option value="false">未创建</option>
          </Select>
        </Field>
      </section>

      {runsQuery.isError ? <ErrorSummary title="测试观测数据不可用" errors={[runsQuery.error instanceof Error ? runsQuery.error.message : '请稍后重试']} /> : null}

      <section className="ai-debug-layout">
        <aside className="ai-debug-run-list" aria-label="AI 调试记录">
          <div className="ai-debug-panel-head">
            <span>AI Turns</span>
            {runsQuery.isFetching ? <Badge>刷新中</Badge> : <Badge tone="default">{runs.length}</Badge>}
          </div>
          {!runs.length ? <EmptyState title="暂无调试记录" description="有 WebChat AI turn 后这里会自动出现。" /> : null}
          {runs.map((run) => <RunCard key={run.id} run={run} active={selectedRun?.ai_turn_id === run.ai_turn_id} onSelect={() => { setSelectedTurnId(run.ai_turn_id); setLastFinding(null) }} />)}
        </aside>

        <section className="ai-debug-detail" aria-label="AI 调试详情">
          <div className="ai-debug-panel">
            <div className="ai-debug-panel-head">
              <span>本轮结论</span>
              {selectedRun ? <Badge tone={toneForRun(selectedRun)}>{sanitizeDisplayText(selectedRun.status)}</Badge> : null}
            </div>
            <SummaryTiles run={selectedRun} bundle={bundle} />
          </div>

          <div className="ai-debug-two-col">
            <div className="ai-debug-panel">
              <div className="ai-debug-panel-head"><span>工具调用</span></div>
              <ToolCalls bundle={bundle} />
            </div>
            <div className="ai-debug-panel">
              <div className="ai-debug-panel-head"><span>策略与隐私</span></div>
              <div className="ai-debug-policy-list">
                <div><span>历史 AI 作为事实</span><strong>禁止</strong></div>
                <div><span>客户说法作为事实</span><strong>禁止</strong></div>
                <div><span>KB 回答实时物流</span><strong>禁止</strong></div>
                <div><span>Live tracking allowed</span><strong>{bundle?.policy?.live_tracking_answer_allowed ? '允许' : '禁止'}</strong></div>
                <div><span>Privacy OK</span><strong>{bundle?.privacy && Object.values(bundle.privacy).every((v) => v === false) ? '通过' : '需检查'}</strong></div>
              </div>
            </div>
          </div>

          <div className="ai-debug-panel">
            <div className="ai-debug-panel-head"><span>事件时间线</span></div>
            <Timeline bundle={bundle} />
          </div>

          <div className="ai-debug-panel">
            <div className="ai-debug-panel-head">
              <span>测试问题沉淀</span>
              {lastFinding ? <Badge tone="success">Finding #{lastFinding.id}</Badge> : null}
            </div>
            <FindingForm aiTurnId={selectedRun?.ai_turn_id} onSaved={(finding) => setLastFinding(finding)} />
            {lastFinding ? (
              <div className="ai-debug-eval-action">
                <Button variant="secondary" disabled={evalMutation.isPending} onClick={() => evalMutation.mutate()}>{evalMutation.isPending ? '生成中…' : '保存为回归案例'}</Button>
                {evalMutation.data ? <Badge tone="success">{sanitizeDisplayText(String(evalMutation.data.case_key || '已创建'))}</Badge> : null}
              </div>
            ) : null}
          </div>

          <div className="ai-debug-panel">
            <div className="ai-debug-panel-head">
              <span>Debug Bundle</span>
              <Button variant="ghost" onClick={() => setShowJson((value) => !value)}>{showJson ? '收起 JSON' : '查看 JSON'}</Button>
            </div>
            {bundleQuery.isError ? <ErrorSummary title="Debug Bundle 不可用" errors={[bundleQuery.error instanceof Error ? bundleQuery.error.message : '请稍后重试']} /> : null}
            {showJson ? <pre className="ai-debug-json">{prettyJson(bundle)}</pre> : <EmptyState title="Bundle 已就绪" description="点击右上角复制完整脱敏 Debug Bundle。" />}
          </div>
        </section>
      </section>
    </main>
  )
}
