import { useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { routeAccess } from '@/lib/rbac'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import type { BadgeTone, KnowledgeChunkHit, KnowledgeStudioConflict } from '@/lib/types'

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'danger' || value === 'warning' || value === 'success' ? value : 'default'
}

function statusTone(value: string): BadgeTone {
  if (value === 'implemented') return 'success'
  if (value === 'linked') return 'warning'
  if (value === 'not_implemented') return 'danger'
  return 'default'
}

function itemTone(status: string, hasConflict: boolean, publishReady: boolean): BadgeTone {
  if (hasConflict || status === 'archived') return 'danger'
  if (publishReady || status === 'draft') return 'warning'
  if (status === 'active') return 'success'
  return 'default'
}

function KnowledgeStudioPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh()
  const [retrievalQuery, setRetrievalQuery] = useState('POD proof of delivery')
  const [conflictQuery, setConflictQuery] = useState('address change')
  const [goldenExpectedItemKey, setGoldenExpectedItemKey] = useState('')
  const [goldenExpectedAnswer, setGoldenExpectedAnswer] = useState('proof of delivery')
  const [goldenForbiddenTerms, setGoldenForbiddenTerms] = useState('manual verification')
  const [goldenMinScore, setGoldenMinScore] = useState(12)
  const studio = useQuery({
    queryKey: ['knowledgeStudio'],
    queryFn: api.knowledgeStudio,
    refetchInterval: autoRefresh.enabled ? 30000 : false,
  })
  const retrieval = useMutation({
    mutationFn: () => api.testKnowledgeRetrieval({ q: retrievalQuery, channel: 'webchat', audience_scope: 'customer', limit: 5 }),
  })
  const conflictCheck = useMutation({
    mutationFn: () => api.checkKnowledgeConflicts({ q: conflictQuery, channel: 'webchat', audience_scope: 'customer', limit: 12 }),
  })
  const goldenTest = useMutation({
    mutationFn: () => api.runKnowledgeGoldenTest({
      q: retrievalQuery,
      channel: 'webchat',
      audience_scope: 'customer',
      limit: 5,
      expected_item_key: goldenExpectedItemKey.trim() || null,
      expected_answer_contains: goldenExpectedAnswer.trim() || null,
      forbidden_answer_terms: goldenForbiddenTerms.split(',').map((item) => item.trim()).filter(Boolean),
      min_score: Number.isFinite(goldenMinScore) ? goldenMinScore : 12,
    }),
  })

  const goTarget = (href: string) => {
    if (href === '/ai-control') navigate({ to: '/ai-control' })
    else if (href === '/qa-training') navigate({ to: '/qa-training' })
    else navigate({ to: '/knowledge-studio' })
  }

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['knowledgeStudio'] }),
      client.invalidateQueries({ queryKey: ['controlTower'] }),
      client.invalidateQueries({ queryKey: ['qaTraining'] }),
    ])
  }

  const suggestedQueries = useMemo(() => {
    const items = studio.data?.items ?? []
    return items
      .filter((item) => item.retrieval_test_ready)
      .map((item) => item.title)
      .slice(0, 3)
  }, [studio.data?.items])
  const displayedConflicts: KnowledgeStudioConflict[] = conflictCheck.data?.conflicts ?? studio.data?.conflicts ?? []

  return (
    <AppShell>
      <PageHeader
        eyebrow="Knowledge Studio"
        title="Knowledge Studio / 知识配置与发布"
        description="AI Ops 从真实 KnowledgeItem、KnowledgeChunk 和版本历史里查看草稿、上传解析、检索测试、冲突风险、发布和回滚状态。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => void refresh()} disabled={studio.isFetching}>刷新</Button><Button variant="primary" onClick={() => navigate({ to: '/ai-control' })}>编辑知识</Button></div>}
      />

      <RequireCapability requirement={routeAccess['/knowledge-studio']}>
        {studio.isLoading ? <Skeleton lines={6} /> : null}
        {studio.isError ? <ErrorSummary title="Knowledge Studio 加载失败" errors={[studio.error instanceof Error ? studio.error.message : '请稍后重试']} action={<Button variant="secondary" onClick={() => void refresh()}>重试</Button>} /> : null}
        {studio.data ? (
          <div className="stack" data-testid="knowledge-studio-template-blocks">
            <div className="metrics-grid metrics-grid-wide" data-testid="knowledge-studio-real-kpis">
              {studio.data.kpis.map((item) => (
                <div className="stack" key={item.key}>
                  <MetricCard label={item.label} value={item.value} hint={item.hint} />
                  <Badge tone={safeTone(item.tone)}>{labelize(item.tone)}</Badge>
                </div>
              ))}
            </div>

            <Card className="soft" data-testid="knowledge-studio-asset-library">
              <CardHeader title="Asset Library / Release Readiness" subtitle="读取真实 KnowledgeItem，不使用前端 fixture；冲突和发布状态由后端 read-model 派生。" />
              <CardBody>
                <DataTable
                  columns={['知识', '范围', '状态', '发布 / 索引', '发布准备', '证据', '入口']}
                  rows={studio.data.items.map((item) => [
                    <div className="stack"><strong>{sanitizeDisplayText(item.title)}</strong><small>{sanitizeDisplayText(item.item_key)}</small></div>,
                    <div className="stack"><span>{sanitizeDisplayText(item.channel || 'global')} · {sanitizeDisplayText(item.audience_scope)}</span><small>{sanitizeDisplayText(item.language || 'global')}</small></div>,
                    <div className="badges"><Badge tone={itemTone(item.status, item.has_conflict, item.publish_ready)}>{labelize(item.status)}</Badge><Badge>{sanitizeDisplayText(item.source_type)}</Badge><Badge>{sanitizeDisplayText(item.knowledge_kind)}</Badge></div>,
                    <div className="stack"><span>v{item.published_version} / index {item.indexed_version}</span><small>{item.chunk_count} chunks</small></div>,
                    <div className="badges"><Badge tone={item.draft_ready ? 'success' : 'warning'}>{item.draft_ready ? 'draft ready' : 'draft missing'}</Badge><Badge tone={item.publish_ready ? 'success' : item.has_conflict ? 'danger' : 'warning'}>{item.publish_ready ? 'publish ready' : item.has_conflict ? 'conflict' : 'blocked'}</Badge></div>,
                    sanitizeDisplayText(item.evidence),
                    <Button variant="secondary" onClick={() => goTarget(item.href)}>打开</Button>,
                  ])}
                  empty={<EmptyState title="还没有知识条目" description="通过 AI 规则里的 Knowledge 编辑器创建第一条知识。" reason="空知识库不会被运行时检索，也不会影响客户回复。" />}
                />
              </CardBody>
            </Card>

            <div className="page-grid split-grid-wide">
              <Card data-testid="knowledge-studio-retrieval-test">
                <CardHeader title="Retrieval Test / Runtime Evidence" subtitle="调用真实 /api/knowledge-items/retrieve-test，只命中已发布并索引的知识。" />
                <CardBody>
                  <div className="stack">
                    <Field label="客户问题"><Input value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="POD 是什么意思？" /></Field>
                    {suggestedQueries.length ? <div className="badges">{suggestedQueries.map((query) => <Button key={query} variant="secondary" onClick={() => setRetrievalQuery(query)}>{sanitizeDisplayText(query)}</Button>)}</div> : null}
                    <div className="button-row"><Button variant="primary" disabled={!retrievalQuery.trim() || retrieval.isPending} onClick={() => retrieval.mutate()}>测试已发布知识</Button></div>
                    {retrieval.isError ? <ErrorSummary title="检索测试失败" errors={[retrieval.error instanceof Error ? retrieval.error.message : '请检查权限或稍后重试']} /> : null}
                    {retrieval.data ? (
                      <div className="kv-grid">
                        <div className="kv"><label>候选</label><strong>{retrieval.data.candidate_count ?? 0} / {retrieval.data.total}</strong></div>
                        <div className="kv"><label>可注入事实</label><strong>{retrieval.data.grounding_would_apply ? '是' : '否'}</strong></div>
                        <div className="kv"><label>语言</label><strong>{sanitizeDisplayText(retrieval.data.query_analysis?.language || '-')}</strong></div>
                      </div>
                    ) : null}
                    <div className="list">
                      {(retrieval.data?.hits ?? []).map((hit: KnowledgeChunkHit) => (
                        <div key={`${hit.item_key}-${hit.chunk_index}`} className="list-item">
                          <div className="badges"><Badge tone="success">score {hit.score}</Badge><Badge>v{hit.published_version}</Badge><Badge>{sanitizeDisplayText(hit.answer_mode || 'guided_answer')}</Badge></div>
                          <strong>{sanitizeDisplayText(hit.title)}</strong>
                          <div className="section-subtitle">{sanitizeDisplayText(hit.item_key)} · section {hit.chunk_index + 1}</div>
                          {hit.matched_terms?.length ? <div className="badges">{hit.matched_terms.map((term) => <Badge key={term}>{sanitizeDisplayText(term)}</Badge>)}</div> : null}
                          {hit.direct_answer ? <div className="message" data-role="agent">{sanitizeDisplayText(hit.direct_answer)}</div> : null}
                          <div className="message" data-role="assistant">{sanitizeDisplayText(hit.text)}</div>
                        </div>
                      ))}
                      {retrieval.data && !retrieval.data.hits.length ? <EmptyState title="没有命中可注入知识" description="当前问题没有匹配到已发布并索引的知识。" reason="系统不会读取草稿或错误 scope 的知识来补答案。" /> : null}
                    </div>
                    <div className="list-item" data-testid="knowledge-studio-golden-test">
                      <div className="message-head"><strong>Golden Test Command</strong><Badge tone={goldenTest.data?.passed ? 'success' : goldenTest.data ? 'danger' : 'default'}>{goldenTest.data ? (goldenTest.data.passed ? 'passed' : 'failed') : 'ready'}</Badge></div>
                      <div className="form-grid">
                        <Field label="Expected item_key"><Input value={goldenExpectedItemKey} onChange={(event) => setGoldenExpectedItemKey(event.target.value)} placeholder="knowledge.studio.pod.document" /></Field>
                        <Field label="Expected answer"><Input value={goldenExpectedAnswer} onChange={(event) => setGoldenExpectedAnswer(event.target.value)} placeholder="proof of delivery" /></Field>
                        <Field label="Forbidden terms"><Input value={goldenForbiddenTerms} onChange={(event) => setGoldenForbiddenTerms(event.target.value)} placeholder="manual verification, unsafe answer" /></Field>
                        <Field label="Min score"><Input type="number" min="0" max="1000" value={goldenMinScore} onChange={(event) => setGoldenMinScore(Number(event.target.value))} /></Field>
                      </div>
                      <div className="button-row"><Button variant="primary" disabled={!retrievalQuery.trim() || goldenTest.isPending} onClick={() => goldenTest.mutate()}>运行黄金测试</Button></div>
                      {goldenTest.isError ? <ErrorSummary title="黄金测试失败" errors={[goldenTest.error instanceof Error ? goldenTest.error.message : '请检查权限或稍后重试']} /> : null}
                      {goldenTest.data ? (
                        <DataTable
                          columns={['断言', '结果', 'Expected', 'Actual', 'Evidence']}
                          rows={goldenTest.data.assertions.map((item) => [
                            sanitizeDisplayText(item.label),
                            <Badge tone={item.passed ? 'success' : 'danger'}>{item.passed ? 'pass' : 'fail'}</Badge>,
                            sanitizeDisplayText(item.expected || '-'),
                            sanitizeDisplayText(item.actual || '-'),
                            sanitizeDisplayText(item.evidence),
                          ])}
                        />
                      ) : null}
                    </div>
                  </div>
                </CardBody>
              </Card>

              <Card data-testid="knowledge-studio-conflict-scan">
                <CardHeader title="Conflict Scan" subtitle="调用真实 /api/knowledge-items/conflict-check，按 scope、问题和别名返回 blocker 与 evidence。" />
                <CardBody>
                  <div className="stack">
                    <Field label="冲突关键词"><Input value={conflictQuery} onChange={(event) => setConflictQuery(event.target.value)} placeholder="address change" /></Field>
                    <div className="button-row"><Button variant="primary" disabled={conflictCheck.isPending} onClick={() => conflictCheck.mutate()}>运行冲突检查</Button></div>
                    {conflictCheck.isError ? <ErrorSummary title="冲突检查失败" errors={[conflictCheck.error instanceof Error ? conflictCheck.error.message : '请检查权限或稍后重试']} /> : null}
                    {conflictCheck.data ? <div className="section-subtitle">Checked {formatDateTime(conflictCheck.data.generated_at)} · {conflictCheck.data.total} conflict groups</div> : null}
                  </div>
                  <DataTable
                    columns={['冲突词', 'Scope', '涉及知识', '阻断', '入口']}
                    rows={displayedConflicts.map((item) => [
                      sanitizeDisplayText(item.term),
                      sanitizeDisplayText(item.scope),
                      <div className="stack">{item.item_keys.map((key) => <span key={key}>{sanitizeDisplayText(key)}</span>)}</div>,
                      <Badge tone={item.blocker ? 'danger' : 'warning'}>{item.blocker ? 'blocking' : 'review'}</Badge>,
                      <Button variant="secondary" onClick={() => goTarget(item.href)}>处理</Button>,
                    ])}
                    empty={<EmptyState title="没有检测到同 scope 冲突" description="后端按问题和别名聚合当前知识条目。" />}
                  />
                </CardBody>
              </Card>
            </div>

            <div className="page-grid split-grid-wide">
              <Card data-testid="knowledge-studio-release-lifecycle">
                <CardHeader title="Release Lifecycle" subtitle="对应模板里的 Draft、Ingestion、Retrieval、Conflict、Published 和 Rollback。" />
                <CardBody>
                  <DataTable
                    columns={['步骤', 'Owner', 'Artifact', '数量', '状态', '入口']}
                    rows={studio.data.release_lifecycle.map((item) => [
                      sanitizeDisplayText(item.step),
                      sanitizeDisplayText(item.owner),
                      sanitizeDisplayText(item.artifact),
                      String(item.count),
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{item.enabled ? '打开' : '无权限'}</Button>,
                    ])}
                  />
                </CardBody>
              </Card>

              <Card data-testid="knowledge-studio-template-closure">
                <CardHeader title="v1.7.8 Knowledge Studio 模板块落地状态" subtitle="真实后端已接入的能力和仍缺的 command 在同一处明示。" />
                <CardBody>
                  <DataTable
                    columns={['模板块', '后端契约', '状态', '证据', '入口']}
                    rows={studio.data.template_blocks.map((item) => [
                      sanitizeDisplayText(item.label),
                      sanitizeDisplayText(item.backend_contract),
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                      sanitizeDisplayText(item.evidence),
                      <Button variant="secondary" onClick={() => goTarget(item.href)}>查看</Button>,
                    ])}
                  />
                  <div className="section-subtitle" style={{ marginTop: 12 }}>Generated {formatDateTime(studio.data.generated_at)} · conflict endpoint {sanitizeDisplayText(String(studio.data.facts.dedicated_conflict_check_endpoint))} · golden tests {sanitizeDisplayText(String(studio.data.facts.dedicated_golden_test_endpoint))}</div>
                </CardBody>
              </Card>
            </div>
          </div>
        ) : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/knowledge-studio',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: KnowledgeStudioPage,
})
